---
title: MySQL 8.0 基于 WriteSet 的并行复制源码解析
date: 2018-5-23
---
在之前的文章 [MySQL 并行复制演进及 MySQL 8.0 中基于 WriteSet 的优化](/2018/05/11/mysql8-writeset-based-replica) 介绍了MySQL 8.0 中WriteSet 的原理和启用条件，本篇将通过源码来追踪 WriteSet 具体是如何实现的。

# 相关数据结构
和 WriteSet 相关的数据结构主要定义在以下几个头文件及对应的.cc文件中。

5.7 中就引入的逻辑锁的定义被移到 `sql/rpl_trx_tracking.h` 中，这个模块定义了事务依赖tracking 相关的接口和数据结构
```cpp
class Logical_clock {

 private:
  std::atomic<int64> state; 
  int64 offset;
    // ... 主要是两个变量，一个原子变量 state 表示当前 logical_clock 的绝对值
    // offset 为当前 binlog 文件开始时的 state 值。 
    // 产生新的 binlog 文件时 state 并不会置0，因此需要用一个变量保存当前 binlog 文件开始时的 state
};
```

除此之外，该头文件中还多了三个看着很像的 class 

```cpp
class Commit_order_trx_dependency_tracker {
   public :
    void get_dependency(THD *thd, int64 &sequence_number, int64 &commit_parent);
    // ...
 private: 
  Logical_clock m_max_committed_transaction; 
  Logical_clock m_transaction_counter; 

  // 这边可以看到 5.7 中基于 group commit 的mts中用到的两个变量，因此可以猜测这个类就是实现了5.7中基于组提交的 mts
  // ...
}

class Writeset_trx_dependency_tracker {
 public:
  Writeset_trx_dependency_tracker(uint64 max_history_size)
      : m_opt_max_history_size(max_history_size), m_writeset_history_start(0) {}
  void get_dependency(THD *thd, int64 &sequence_number, int64 &commit_parent);
  void rotate(int64 start);

  /* option opt_binlog_transaction_dependency_history_size */
  ulong m_opt_max_history_size; 
 private:  
  typedef std::map<uint64, int64> Writeset_history;
  Writeset_history m_writeset_history;
};

class Writeset_session_trx_dependency_tracker {
 public: 
  void get_dependency(THD *thd, int64 &sequence_number, int64 &commit_parent);
};

```

这三个class 从名字看和 MySQL 8.0 新引入的参数 `binlog_transaction_depandency_tracking` 的三个取值 commit_order , writeset , writeset_session 是一一对应的，实际上也的确如此。

这三个class都有一个相同签名的方法 get_dependency ，从名字上看很容易明白含义，获取事务的依赖关系，也就是计算 sequence_number 和 commit_parent 这两个重要的 binlog 中的变量的值。 可以看到这个方法接受三个参数，第一个 THD *thd 是当前执行事务的线程，后面两个就是 binlog 中的sequence_number 和 commit_parent ，通过引用的方式传入参数，在方法内部修改这两个变量的值。

`sql/rpl_trx_tracking.h` 再往下就是一个枚举的定义和一个代理类 

```c++
enum enum_binlog_transaction_dependency_tracking { 
  DEPENDENCY_TRACKING_COMMIT_ORDER = 0, 
  DEPENDENCY_TRACKING_WRITESET = 1, 
  DEPENDENCY_TRACKING_WRITESET_SESSION = 2
};

class Transaction_dependency_tracker {
 public:
  Transaction_dependency_tracker()
      : m_opt_tracking_mode(DEPENDENCY_TRACKING_COMMIT_ORDER),
        m_writeset(25000) {}
  void get_dependency(THD *thd, int64 &sequence_number, int64 &commit_parent);
  void tracking_mode_changed();
  void update_max_committed(THD *thd);
  int64 get_max_committed_timestamp();
  int64 step();
  void rotate();
 public:
  /* option opt_binlog_transaction_dependency_tracking */
  long m_opt_tracking_mode;
  Writeset_trx_dependency_tracker *get_writeset() { return &m_writeset; }
 private:
  Writeset_trx_dependency_tracker m_writeset;
  Commit_order_trx_dependency_tracker m_commit_order;
  Writeset_session_trx_dependency_tracker m_writeset_session;
};
```

可以看到枚举定义分别对应 MySQL 8.0 中三种不同类型的事务依赖 tracking 的方式。实际在Binlog生成阶段是调用 Transaction_dependency_tracker 中的方法，该类再根据 m_opt_tracking_mode 决定具体调用哪种实现的 get_dependency 方法。这边也可以看到，8.0中默认的tracking 方式为 commit_orderer，默认 writeset_history的大小为 25000 , 和官方文档一致。

以上是接口层面的定义，WriteSet 具体数据结构的定义在 `sql/rpl_transaction_write_set_ctx.h` 头文件，定义如下:

```cpp
class Rpl_transaction_write_set_ctx {
 public:
	// getter && setter .....
 private:
  std::vector<uint64> write_set;
  bool m_has_missing_keys; // 标记事务是否更新了无主键也无唯一索引的行
  bool m_has_related_foreign_keys;  // 标记事务是否更新的行是其他表的外键
  std::map<std::string, size_t> savepoint; // 标记事务的savepoint 点，事务回滚时回滚 writeset
  std::list<std::map<std::string, size_t>> savepoint_list;
};
```

 从类名中的 transaction 和 ctx 可以看到，这个类是和一个事务绑定的，记录的是事务执行过程中修改的行的write_set，具体的类型为 `std::vector<uint64>`  ，这个向量中保存的是修改的行的Key的Hash值。

此外有个savepoint，这是个map，key对应的是事务执行 `savepoint identifier ;` 时的名字，value对应的是 write_set 中的下标。当事务rollback的时候，就可以查询到 savepoint 点对应的 writeset 向量的下标并删除下标到向量结尾的值。

最后一个需要关注的头文件是 `sql/rpl_write_set_handler.h` 这个头文件定义了连个方法。

```cpp
// 转换 transaction_write_set_extraction 变量枚举和字符串方法
const char *get_write_set_algorithm_string(unsigned int algorithm);

// 将当前正在执行事务修改行的主键的Hash值添加到事务的 writeset 中
void add_pke(TABLE *table, THD *thd);
```

主要关注第二个方法，该方法在事务执行的过程中调用，每修改一行，在产生 binlog 前调用该方法，提取当前修改的行的主键或唯一索引，并将其HASH值添加到事务的 writeset 中。