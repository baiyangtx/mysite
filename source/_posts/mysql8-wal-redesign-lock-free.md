---
title: MySQL 8.0 Innodb 无锁设计的 Redo Log
date: 2018-12-20
---

# 0. 引言

MySQL 8.0 中一个重要的特性是对 Redo Log Buffer 的重构，通过引入两个新的数据结构 `recent_written` 和 `recent_closed` 移除了之前的两个热点锁 `log_sys_t::mutex` 和 `log_sys_t::flush_order_mutex`。 这种无锁化的重构使得不同的线程在写入 redo_log_buffer 时得以并行写入， 但因此带来了 log_buffer 不再按 LSN 增长的顺序写入的问题，以及 flush_list 中的脏页不再严格保证 LSN 的递增顺序。 本文将介绍 MySQL 8.0 中对 log_buffer 相关代码的重构，并介绍并发写 log_buffer 引入问题的解决办法。

# 1. MySQL Redo Log 系统概述

Redo Log 又被称为 WAL ( Write Ahead Log) , 是Innodb存储引擎实现事务持久性的关键。 
在InnoDB存储引擎中，事务执行过程被分割成一个个 MTR (Mini TRansaction), 每个MTR在执行过程中，对数据页的更改会产生对应的日志称，这个日志就是Redo Log。 
在持久化数据页到磁盘之前，必须保证 Redo Log 已经写入到磁盘中。实际上，事务在提交时，只要保证 redo log 被持久化，就可以保证事务的持久化。由于 redo log 在持久化过程中顺序写文件的特性，使得持久化 redo log 的代价要远远小于持久化数据页，因此通常情况下，数据页的持久化要落后于redo log。

每个RedoLog都有一个对应的序号 LSN (Log Sequence Number), 同时数据页上也会记录修改了该数据页的redo log的LSN，当数据页持久化到磁盘上时，就不再需要这个数据页记录的LSN之前的Redo 日志。这时，这个LSN被称作 checkpoint .

InnoDb 存储引擎在 内存中维护了一个全局的 redo log buffer 用以缓存对 redo log的修改，mtr事务在提交的时候，会将mtr事务执行过程中产生的本地日志 copy 到全局 redo log buffer 中，并将mtr 执行过程中修改的数据页（被称做脏页 dirty page）加入到一个全局的队列中 flush list。 InnoDB存储引擎会根据不同的策略将 redo log buffer 中的日志落盘，或将 flush list 中的脏页刷盘并推进 checkpoint.

在脏页落盘以及checkpoint 推进的过程中，需要严格保证 redo 日志先落盘再刷脏页的顺序，在MySQL 8 之前，InnoDB 存储引擎严格的保证 MTR 写入 redo log buffer的顺序是按照 LSN 递增的顺序，以及 flush list 中的脏页按LSN递增顺序排序。
在多线程并发写入 redo log buffer 以及 flush list 时，这一约束是通过两个全局锁 `log_sys_t::mutex` 和 `log_sys_t::flush_order_mutex` 实现的。

# 2. MySQL 5.7 中 MTR的提交过程

在MySQL 5.7中，Redo log写入 全局的 redo log buffer 以及将脏页添加到 flush list 的操作均在 mtr 的提交过程中完成的，可以用如下伪代码表示：

```
mtr::Command::commit(){
    uint64 len = prepare_write() ;  
    # 这里调用 mutex_enter(log_sys->mutex) 加全局日志锁
    
    finish_write(len) ;
    # 这里会 copy mtr 事务中的redo 到全局 redo log buffer 并获取到 start_lsn 和 end_lsn
    # 由于在 log_sys->mutex 保护范围内，这里写入 redo log buffer 的LSN必定的全局递增的

    mutex_enter(log_sys->flush_order_mutex) ;
    mutex_exit( log_sys->mutex )
    # 这里先获取 flush_order_mutex 再释放全局日志锁 mutex，保证只有刚写入 redo log buffer 的线程可以写入 flush list 

    release_block()
    # 调用 add_dirty_page_to_flush_list 将脏页加入到 flush list 中

    mutex_exit(log_sys->flush_order_mutex);
    # 释放 flush_order_mutex 
}
```

MySQL官方博客中有一张图可以很好的展示了这个过程 

![](/postimgs/mysql8-wal-redesign-lock-free/redo-old-design-flow.png)

# 3. MySQL 8 中的无锁化设计

从上面的代码中可以看到，在有多个MTR事务并发提交的时候，实际在这些事务是串行的完成从本地日志Copy redo 到全局Redo Log Buffer 以及添加 Dirty Page 到 Flush list 的。
这里的串行操作就是整个MTR 提交过程的瓶颈，如果这里可以改成并行，想必是可以提高MTR的提交效率。

但是串行化的提交可以严格保证redo Log的连续性以及 flush list 中Page修改LSN的递增，这两个约束使得将 redo log 和 脏页刷入磁盘的行为很简单。
只要按顺序将 redo log buffer 中的内容写入文件，以及按flush list 的顺序将脏页刷入表空间，并推进 checkpoint 即可。当MTR不再以串行的方式提交的时候，会导致以下问题需要解决：

**1.** MTR串行的 copy 本地日志到全局 redo log buffer 可以保证每个MTR的日志在 redo log buffer中都是连续的不会分割。 当并行 copy 日志的时候，需要有额外的手段保证mtr的日志copy到 redo log buffer 后仍然连续。MySQL 8.0 中使用一个全局的原子变量 `log_t::sn` 在copy 数据前为MTR在 redo log buffer 中预留好需要的位置，这样并行copy 数据到 redo log buffer 时就不会相互干扰。

**2.** 由于多个MTR并行 copy 数据到 redo log buffer, 那必然会有一些MTR copy的快一些，有些MTR copy 的比较慢，这时候 redo log buffer 中可能会有空洞，那么就需要一种方法来确定哪些 redo log buffer 中的内容可以写入文件。MySQL 8.0 中引入了新的数据结构 `Link_buf` 解决了这个问题。

![并发写入redo log buffer 带来的空洞问题](/postimgs/mysql8-wal-redesign-lock-free/concurrent-copy-redo-to-log-buffer.png)

**3.** 并行的添加脏页到 flush list 会打破 flush list 中每个数据页修改LSN的单调性的约束，如果仍然按 flush list 中的顺序将脏页落盘，那如何确定 checkpoint 的位置。 

下面我们来一一讨论以上三个问题。

## 3-1. MTR复制日志到redo log buffer 的无锁化

在MySQL 8.0 中， MTR的提交部分可以用如下伪代码表示:

```
void mtr_t::Command:execute(){
    uint len = prepare_write();
    # 获取redo log 的大小

    auto handle = log_buffer_reserve(*log_sys, len);
    # 为 redo log 在全局的 redo log buffer 中分配空间

    m_impl->m_log.for_each_block(write_log);
    # 对每个 block 执行真正的 copy 操作，将 redo log copy到 redo log buffer 中

    log_buffer_write_completed_before_dirty_pages_added(*log_sys, handle);
    # 等待 flush list 中的无序度降到阈值以内 recent_closed.has_space(start_lsn) 

    add_dirty_blocks_to_flush_list(handle.start_lsn, handle.end_lsn);
    # 将 脏页添加到 flush list 中

    log_buffer_write_completed_and_dirty_pages_added(*log_sys, handle);
    # 跟新脏页的刷入信息 recent_closed.add_link(start_lsn, end_lsn)
}
```

其中 redo log的无锁化的关键在于 `log_buffer_reserve(*log_sys, len)` 这个函数, 其中关键的代码只有两句：

```
Log_handle log_buffer_reserve(log_t &log, size_t len) {
    const sn_t start_sn = log.sn.fetch_add(len);
    const sn_t end_sn = start_sn + len;

    # 其中 log.sn 就是一个全局的 std::atomic<uint64> 原子类型。它表示当前 redo log buffer 中空余位置。
    # 通过原子的修改并获取该变量的值，mtr 线程就可以在 redo log buffer 中为本地 redo log 分配空间
    # 这样当多个 mtr 事务开始真正 copy 数据时，就不会发生冲突
    # ...
}
```

## 3-2. Log Buffer 空洞问题

预分配的方式可以使多个 mtr 不冲突的copy数据到 redo log buffer，但由于有些线程快一些，有些线程慢一些，必然会造成 redo log buffer 的空洞问题，这个使得 redo log buffer 刷入到磁盘的行为变得复杂。


![并发写入redo log buffer 带来的空洞问题](/postimgs/mysql8-wal-redesign-lock-free/concurrent-copy-redo-to-log-buffer.png)

如上图所示，redo log buffer 中第一个和第三个线程已经完成了 redo log 的写入，第二个线程正在写入到redo log buffer 中，这个时候是不能将三个线程的redo 都落盘的。MySQL 8.0 中引入了一个数据结构 Link_buf 解决这个问题。

Link_buf 实际上是一个定长数组，并保证数组的每个元素的更新是原子性的，并以环形的方式复用已经释放的空间。同时内部维护了一个变量M 表示当前最大可达的LSN， Link_buf 的结构示意图如下所示

![Link_buf示意图](/postimgs/mysql8-wal-redesign-lock-free/link_buf.png)

redo log buffer 内部维护了两个 Link_buf 类型的变量 `recent_written` 和 `recent_closed` 来维护 redo log buffer 和 flush list 的空洞信息。

对于redo log buffer，buffer 的使用情况和 `recent_written` 的对应关系如下图所示：

![recent_written 写入前](/postimgs/mysql8-wal-redesign-lock-free/recent-written-before-write.png)

`buf_ready_for_write_lsn` 这个变量维护的是可以保证无空洞的最大 LSN 值。

当第一个空洞位置的数据被写入成功后，写入数据的 mtr 通过调用 `log.recent_written.add_link(start_lsn, end_lsn) ` 将 recent_written 内部状态更新为如下图所示的样子。 这部分代码在 log0log.cc 文件的 `log_buffer_write_completed` 方法中。

![recent_written 写入后](/postimgs/mysql8-wal-redesign-lock-free/recent-written-after-write.png)

每次修改 recent_written 后，都会触发一个独立的线程 `log_writer` 向后扫描 recent_written 并更新 `buf_ready_for_write_lsn` 值。 `log_writer` 线程实际上就是执行日志写入到文件的线程。由 `log_writer` 线程扫描后的 `recent_written` 变量内部如下图所示.

![recent_written 推进后](/postimgs/mysql8-wal-redesign-lock-free/recent-written-pushed.png)

这样就很好的解决了MTR并发写入log_buffer 造成的空洞问题。

### 3-3 关于更多落盘的细节

在 MySQL 8 中，Redo log 的落盘过程交由两个独立的线程完成，分别是 `log_writer` 和 `log_flusher`, 前者负责将 redo log buffer 中的数据写入到 OS Cache 中， 后者负责不停的执行 `fsync` 操作将 OS Cache 中的数据真正的写入到磁盘里。两个线程通过一个全局的原子变量 `log_t::write_lsn` 同步，write_lsn 表示当前已经写入到磁盘的Redo log最大的LSN。

![log_writer 和 log_flusher](/postimgs/mysql8-wal-redesign-lock-free/log_writer-and_log_flusher.png)

log buffer 中的 redo log的落盘不需要由用户线程关系，用户线程只需要在事务提交的时候，根据 `innodb_flush_log_at_trx_commit` 定义的不同行为，
等待 `log_writer` 或 `log_flusher`的通知即可。

`log_writer` 线程会在监听到 `recent_written` 推进后，将log_buffer 中大于 `log_t::write_lsn` 小于 `buf_ready_for_write_lsn` 的 redo log 刷入到 OS Cache 中，并更新 `log_t::write_lsn`。 

`log_flusher` 线程则在监听到 write_lsn 更新后调用一次 fsync() 并更新 `flushed_to_disk_lsn` 为最新fsync到文件的值。

![log_writer 和 log_flusher](/postimgs/mysql8-wal-redesign-lock-free/log_writer-and_log_flusher-sync.png)


在这种设计模式下，用户线程只负责写日志到 log_buffer 中，日志的刷新和落盘是完全异步的，根据 `innodb_flush_log_at_trx_commit` 定义的不同行为，用户线程在事务提交时需要等待日志写入操作系统缓存或磁盘。

在8.0 之前，是由用户线程触发fsync 或者等先提交的线程执行fsync( Group Commit 行为)， 而在MySQL 8.0 中，用户线程只需要等待 `flushed_to_disk_lsn` 足够大即可。

![8.0 之前用户线程触发 fsync ](/postimgs/mysql8-wal-redesign-lock-free/8-before-user-thread-wait-fsync.png)

8.0 中采用了一个分片的消息队列来通知用户线程，比如用户线程需要等待 `flushed_to_disk_lsn >= X` 那么就会加入到X所属的消息队列。分片可以有效的降低消息同步的损耗以及一次需要通知的线程数。

![分片的通知消息队列 ](/postimgs/mysql8-wal-redesign-lock-free/flushed_to_disk_lsn_wait_queue.png)

在8.0 中，由后台线程 `log_flush_notifier` 通知等待的用户线程，用户线程、`log_writer`、`log_flusher`、`log_flush_notifier` 四个线程之间的同步关系为。

![8.0中用户线程、`log_writer`、`log_flusher`、`log_flush_notifier` 四个线程之间的同步关系](/postimgs/mysql8-wal-redesign-lock-free/8-after-user-thread-wait-fsync.png)

8.0 中为了避免用户线程在陷入等待状态后立即被唤醒，用户线程会在等待前做自旋以检查等待条件。8.0中新增加了两个Dynamic Variable: `innodb_log_spin_cpu_abs_lwm` 和`innodb_log_spin_cpu_pct_hwm` 控制执行自旋操作时CPU的水位，以免自旋操作占用了太多的CPU。


## 3-4. flush list 并发控制以及check point 推进

回到上面的MTR提交的伪代码，可以看到在将 redo log 写入全局的 log buffer 中以后， mtr立即开始了将脏页加入到flush list的步骤，其过程分为三个步骤。

```
log_buffer_write_completed_before_dirty_pages_added(*log_sys, handle);
# 等待 flush list 中的无序度降到阈值以内 recent_closed.has_space(start_lsn) 

add_dirty_blocks_to_flush_list(handle.start_lsn, handle.end_lsn);
# 将 脏页添加到 flush list 中

log_buffer_write_completed_and_dirty_pages_added(*log_sys, handle);
# 跟新脏页的刷入信息 recent_closed.add_link(start_lsn, end_lsn)
```

这里同样是通过一个 Link_Buf 类型的无锁结构 `recent_closed` 来跟踪处理 flush list 并发写入状态。
假设MTR在提交时产生的redo log的范围是[start_lsn, end_lsn]，
MTR在将这些redo 对应的脏页加入到某个 flush list 后，立即将 start_lsn 到 end_lsn 这段标记在 `recent_closed` 结构中。
`recent_closed` 同样在内部维护了变量M，M对应着一个LSN，表示所有小于该LSN的脏页都加入到了 flush list中。 
而与 redo log 写入不同的是，MTR在写入flush list之前，需要等待M值与 start_lsn相差不是太多才可以写入。这是为了将 flush list上的空洞控制在一个范围之内，这个过程的示意图如下：

![MTR写入flush list的过程 ](/postimgs/mysql8-wal-redesign-lock-free/recent-closen.png)

MTR在写入到flush list之前，等待M值与 start_lsn 的相差范围是一个常数L，这个常数度量了flush list中的无序度，它使得checkpoint的确定变得简单
（实际代码中，L值就是recent_closed内部容量大小）。


   每次MTR将脏页加入到 flush list 后，都会将对应的redo log的

假设某个MTR提交，它先将本地的 redo log 复制到 redo log buffer， 假设这批 redo log 的范围是 [start_lsn, end_lsn]，
这时MTR准备将这批REDO修改的脏页加入到 flush list 中，MTR线程会等待 flush list 中有空洞的范围小于一个常数L后，才会将脏页写入到 flush list中。


MTR在写入了flush list的
与 log_buffer 类似，`recent_closed` 同时也维护了一个最大值M，M表示所有
与写 log_buffer 不同，在对flush list的写入过程中，用户线程需要等待
