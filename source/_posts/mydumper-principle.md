---
title: Mydumper原理-并行获取MySQL一致性数据
date: 2016-9-4
---


相对于MySQL官方提供的逻辑备份工具 mysqldump ， mydumper最大的特点就是可以采用多线程并行备份，大大提高了数据导出的速度。这里对mydumper的工作原理做个分析，看一下mydumper如何巧妙的利用Innodb引擎提供的MVCC版本控制的功能，实现多线程并发获取一致性数据。

这里一致性数据指的是在某个时间点，导出的数据与导出的Binlog文件信息相匹配，如果导出了多张表的数据，这些不同表之间的数据都是同一个时间点的数据。

在mydumper进行备份的时候，由一个主线程以及多个备份线程完成。其主线程的流程是：

1. 连接数据库
2. FLUSH TABLES WITH READ LOCK 将脏页刷新到磁盘并获得只读锁
3. START TRANSACTION /*!40108 WITH CONSISTENT SNAPSHOT */ 开启事物并获取一致性快照
4. SHOW MASTER STATUS  获得binlog信息
5. 创建子线程并连接数据库
6. 为子线程分配任务并push到队列中
7. UNLOCK TABLES /* FTWRL */ 释放锁


子线程的主要流程是：
1. 连接数据库
2. SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE 
3. START TRANSACTION /*!40108 WITH CONSISTENT SNAPSHOT */
4. 从队列中pop任务并执行


上述两个线程的流程的关系如图

![image](/postimgs/mydumper-principle/mydumper-threads.png)

从图中可以看到，主线程释放锁是在子线程开启事物之后。这里是保证子线程获得的数据一定为一致性数据的关键。
主线程在连接到数据库后立即通过Flush tables with read lock(FTWRL) 操作将脏页刷新到磁盘，并获取一个全局的只读锁，这样便可以保证在锁释放之前由主线程看到的数据是一致的。然后立即通过 Start Transaction with consistent snapshot 创建一个快照读事物，并通过 show master status获取binlog位置信息。
然后创建完成dump任务的子线程并为其分配任务。  

主线程在创建子线程后通过一个异步消息队列 ready 等待子线程准备完毕。 子线程在创建后立即创建到MySQL数据库的连接，然后设置当前事务隔离级别为Repeatable Read。
设置完成之后开始快照读事务。在完成这一系列操作之后，子线程才会通过ready队列告诉主线自己程准备完毕。主线程等待全部子线程准备完毕开启一致性读Snapshot事务后才会释放全局只读锁（Unlock Table）。

如果只有Innodb表，那么只有在创建任务阶段会加锁。但是如果存在MyIsam表或其他不带有MVCC功能的表，那么在这些表的导出任务完成之前都必须对这些表进行加锁。Mydumper本身维护了一个 non_innodb_table 列表，在创建任务阶段会首先为非Innodb表创建任务。同时还维护了一个全局的unlock_table队列以及一个原子计数器 non_innodb_table_counter , 子线程每完成一个非Innodb表的任务便将 non_innodb_table_counter 减一，如果non_innodb_table_counter 值为0 遍通过向 unlock_table 队列push一个消息的方式通知主线程完成了非Innodb表的导出任务可以执行 unlock table操作。

mydumper支持记录级别的并发导出。在记录级别的导出时，主线程在做任务分配的时候会对表进行拆分，为表的一部分记录创建一个任务。这样做一个好处就是当有某个表特别大的时候可以尽可能的利用多线程并发以免某个线程在导出一个大表而其他线程处于空闲状态。在分割时，首先选取主键（PRIMARY KEY）作为分隔依据，如果没有主键则查找有无唯一索引(UNIQUE KEY)。在以上尝试都失败后，再选取一个区分度比较高的字段做为记录划分的依据(通过 show index 结果集中的cardinality的值确定)。

划分的方式比较暴力，直接通过 select min(filed),max(filed) from table 获得划分字段的取值范围，通过 explain select filed from table 获取字段记录的行数，然后通过一个确定的步长获得每一个子任务的执行时的where条件。这种计算方式只支持数字类型的字段。

以上就是mydumper的并发获取一致性数据的方式，其关键在于利用了Innodb表的MVCC功能，可以通过快照读因此只有在任务创建阶段才需要加锁。
