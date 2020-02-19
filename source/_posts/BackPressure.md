---
title: 基于Netty的反压逻辑实现
date: 2020-02-24
tag: ["Netty", "反压", "流控"]
---

Netty是现在最流行的异步编程框架，而在使用异步IO完成业务逻辑时，一个重要的议题就是如果让上下游的业务处理速度匹配，反压（BackPressure）是一种常用的手段。本文将介绍反压的模型以及如何在Netty中实现反压。


## 什么是反压(BackPressure)

反压的问题来自于由多个组件串联工作的架构中，当多个组件以串联的方式组织时，其中上下游生产和消费的速度并不匹配。如果上游传递的消息速度大于下游消费的速度，那么消费端要么会丢弃消息，要么会因内存溢出而崩溃。

![为什么需要流控](/postimgs/back-pressure/why-we-need-back-pressure.png)

这个时候就需要通过某种机制让上游降低生产速率，从而达到上下游的速率匹配，也就是流控。

流控有多种方式可以实现，最简单的方式是在上游直接限制生产速率，这种方式称为静态限速：

![静态限速](/postimgs/back-pressure/simple-control-speed.png)

但是这种方式并不优雅，需要预先估计下游消费者的消费能力，而且当消费者的消费能力出现波动时，无法动态的调整生产者的生产速率。因此动态反馈是一种更加优雅的流控方法。

![动态限速](/postimgs/back-pressure/dymanic-speed-control.png)

这种流控方式类似于自动控制理论中的反馈控制模型，消费者通过不断的给生产者反馈(feedback)，让生产者可以更加主动的调整自己的生产速率，生产者和消费者之间永远以最佳的速率匹配，从而让整个系统在最优的效率下运行。这个过程中既需要正反馈：在消费者有消费能力时通知生产者提高生产速率；又需要负反馈：在消费者消费能力达到饱和时通知生产者降低生产速率。


## TCP的流控与异步IO

TCP连接在设计时自带了一套流控系统，ACK机制、滑动窗口、以及拥塞控制就是一套完整的动态反馈。这里简单的回顾一下TCP的流控机制。

### TCP 的消息确认机制以及滑动窗口

在TCP协议中，每一个发送方的Package都带有唯一编号，因此每一个消息都能够被独立确认，因此发送方可以一次发送多个Package，当接收方接到发送方的消息时，必须回复ACK向发送方确认接收到消息，这个过程是为了保证TCP连接的不丢失的特性。

![TCP协议的ACK机制](/postimgs/back-pressure/tcp-ack.jpg)

因为发送方可以一次发送多个消息，发送方每次发送的连续的消息序号被称为一个窗口，在发送方内部，所有的消息按序号顺序排列后可以分为四类：

1. 已经发送并且已经接受到ACK的消息
2. 已经发送但是未接受到ACK的消息
3. 未发生但是接收方允许发送的消息
4. 未发生并且接收方不允许发送的消息

![TCP协议发送方窗口](/postimgs/back-pressure/tcp-sender-window.png)

其中，2已经发送但是未接收到ACK的消息和3未发生但是接收方允许的消息加在一起，被称为发送端的窗口(Window)。

对于接收端，同样有三种状态的消息：

1. 已经接收
2. 未接受但是准备接收
3. 未接受且不准备接收

由于接受方接收消息后立即回复ACK，这里可以认为不存在已接受但未回复ACK的消息。这里未接受但是准备接受的消息就是接收端窗口，通常等于接收缓冲区的可用大小。

在TCP的协议中，ACK包中带有 WindowsSize 控制段，该字段的含义是接收端的接收窗口大小，接收方通过该字段反馈发送方调整发送窗口的大小。

![TCP协议ACK包帧格式](/postimgs/back-pressure/tcp-ack-frame-format.png)

当发送端接收到接收端的ACK包时，就可以知道当前接收端的接送能力，调整自己的发送窗口大小，从而达到流控的目标。

### 区别滑动窗口与拥塞窗口

拥塞窗口与滑动窗口一样是TCP协议中进行流控的方式，但是拥塞窗口并不在本文讨论的范畴中，二者是有区别的，这里对二者的区别进行比较：

滑动窗口是用于发送端和接收端之间匹配发送数据包能力的，发送端和接收端可以理解为本文讨论的生产者和消费者；而拥塞窗口是发送端和中间网络之间流控的窗口，它不涉及到接受端。可以把滑动窗口理解为接收端的接收能力，而拥塞窗口是中间网络的传输能力。

实际上发送端的发送滑动窗口大小 W = min(awind, cwind) , 其中 awind 是接收端在ACK中反馈的窗口大小，而cwind是发送端通过拥塞控制算法计算出的拥塞窗口大小。

### 同步IO与异步IO

滑动窗口协议可以解决TCP发送端和接收端的处理速率匹配的问题，然而接收端的接收能力不等于数据的处理能力，这个问题通常是由异步IO带来的。

在同步IO中，接收端从接收缓冲区读取数据与数据的处理是顺序的，接收端接收到的数据通常会立即处理，如果处理不了会保留在接收缓冲区中，这样系统就会在TCP接收回复的ACK中通知发送端减少发送窗口大小。

![同步处理逻辑](/postimgs/back-pressure/sync-handle.png)

在同步处理逻辑中，业务线程负责从接收缓冲区读取数据并且进行处理，二者的处理能力是匹配的。而在异步IO中，处理逻辑并不是这样，这里以Netty为例。

![异步处理逻辑](/postimgs/back-pressure/async-handle.png)

在异步IO中，通过Epoll系统调用或Netty框架注册read事件回调，当TCP协议栈向接收缓冲区写入数据后，通过回调调用事件响应函数。在事件响应函数中读取数据，然后提交给同步或异步的业务处理逻辑消费数据。在这个过程中，事件响应函数调用业务处理逻辑的资源耗尽（比如线程池满等原因）无法提交，导致数据丢弃。

因此在异步IO编程时，当Read事件到达时，需要判断当前业务线程的消费能力，如果已经无法消费数据，那么必须从事件轮询中移除回调，并且不再读取数据。同样的，当业务线程有能力处理时，需要重新向事件轮询中注册读事件的回调，不然后续都无法触发读事件。

## Netty 中对反压的支持

### AUTO_READ 属性

Netty 作为一个优秀的异步编程框架，提供了简洁的API达到反压的效果。

反压的核心在于消费端没有能力处理更多数据的时候，不要再从接收缓冲区读取更多数据。Netty对于每个 Channel 提供了一个属性 `AUTO_READ` 以控制当接收缓冲区中有数据时要不要自动触发读事件。这里引用文档的说法

```
channel 在触发某些事件以后(例如 channelActive, channelReadComplete)以后还会自动调用一次 read(), 默认为true
```

其使用方式为

```
    // 为所有channel 默认设置 
    bootstrap.group(group)
        .channel(NioServerSocketChannel.class)
        .option(ChannelOption.TCP_NODELAY, true)
        .option(ChannelOption.AUTO_READ, false)

    // 或为某个channel 单独调用
    channel.setAutoRead(false)
```

从文档结合需求上看，如果业务处理能力达到瓶颈希望不再从缓冲区读取数据，只需要在 channel 上调用 `channel.setAutoRead(false)` 即可。

### 原理剖析

按照文档描述，AUTO_READ 这个属性是用于在 `channelActive` 事件或 `channelReadComplete` 后自动触发一次，`read()` 方法，这里我们先看下read方法做了什么。

查看 Channel.read() 的实现，只有一个默认实现 `AbstractChannel.read()` 。

```
public abstract class AbstractChannel extends DefaultAttributeMap implements Channel {
    // .... 

    // 这里直接调用的是  DefaultChannelPipeline 的read() 方法
    public Channel read(){
        pipeline.read();
        return this;
    }
}
```

继续进入到 `DefaultChannelPipeline` 发现其继续调用的是 `AbstractChannelHandlerContext.read()` 方法；

```
abstract class AbstractChannelHandlerContext extends DefaultAttributeMap implements ChannelHandlerContext {
    // ...
    
    @Override
    public ChannelHandlerContext read() {
        final AbstractChannelHandlerContext next = findContextOutbound();
        EventExecutor executor = next.executor();
        if (executor.inEventLoop()) {
            next.invokeRead();
        } else {
            Runnable task = next.invokeReadTask;
            if (task == null) {
                next.invokeReadTask = task = new Runnable() {
                    @Override
                    public void run() {
                        next.invokeRead();
                    }
                };
            }
            executor.execute(task);
        }
        return this;
    }
}
```

这里可以看到，这里判断当前调用者是否在 eventLoop 线程中，如果在 eventLoop 线程中，直接调用 `AbstractChannelHandlerContext.invokeRead()` 方法，否则则在 eventLoop 的线程中起一个Task调用同样的方法。 read 方法的实现应该就在该 `invokeRead()` 方法中。继续深究这个方法的调用链，发现是在 `DefaultPipeline.HeadContext` 中，通过一个 `Channel.Unsafe` 类调用 `beginRead` 方法。继续追踪其调用到 `AbstractChannel.doBeginRead()` 方法，发现有多个实现类, 分别对应着不同类型的IO方式：

1. AbstractEpollChannel - 对应着在 Linux系统下，通过NativeApi 调用 epoll 系统调用实现的异步IO
2. AbstractNioChannel  - 对应着使用 JavaNio 的实现
3. AbstractOioChannel  - Old IO 对应着使用多线程的 BIO实现

抛开最后一个不看，看前两个实现：

```
abstract class AbstractEpollChannel extends AbstractChannel implements UnixChannel {

    @Override
    protected void doBeginRead() throws Exception {
        // Channel.read() or ChannelHandlerContext.read() was called
        ((AbstractEpollUnsafe) unsafe()).readPending = true;
        setFlag(readFlag);
    }
    void setFlag(int flag) throws IOException {
        if (!isFlagSet(flag)) {
            flags |= flag;
            modifyEvents();
        }
    }
    private void modifyEvents() throws IOException {
        if (isOpen() && isRegistered()) {
            ((EpollEventLoop) eventLoop()).modify(this);
        }
    }
}

class EpollEventLoop {
    void modify(AbstractEpollChannel ch) throws IOException {
        assert inEventLoop();
        Native.epollCtlMod(epollFd, ch.fd().intValue(), ch.flags);
    }
}
// 在EpollChannel中，是通过Native方法将readFlag 注册到 epoll flags 中
```

再看 AbstractNioChannel

```
public abstract class AbstractNioChannel extends AbstractChannel {
    @Override
    protected void doBeginRead() throws Exception {
        // Channel.read() or ChannelHandlerContext.read() was called
        if (inputShutdown) {
            return;
        }
        final SelectionKey selectionKey = this.selectionKey;
        if (!selectionKey.isValid()) {
            return;
        }
        readPending = true;
        final int interestOps = selectionKey.interestOps();
        if ((interestOps & readInterestOp) == 0) {
            selectionKey.interestOps(interestOps | readInterestOp);
        }
    }
}
```

也是将 `readInterestOp` 标志位注册到 `SelectionKey` 中。所以channel.read() 方法并不是真的去底层读取数据，而是将 read事件注册到异步事件循环中。

在了解了read() 方法的作用后，可以看到 `AUTO_READ` 属性是怎样起作用的。在 `DefaultChannelPipeline` 中

```
final class DefaultChannelPipeline implements ChannelPipeline {

    public ChannelPipeline fireChannelActive() {
        head.fireChannelActive();

        if (channel.config().isAutoRead()) {
            channel.read();
        }

        return this;
    }

    public ChannelPipeline fireChannelReadComplete() {
        head.fireChannelReadComplete();
        if (channel.config().isAutoRead()) {
            read();
        }
        return this;
    }
}
```

可以看，在 channelActive 和 channelReadComplete 事件中，如果channel配置了 autoRead, 则会调用 channel.read() 方法注册read事件到eventLoop 上。

对于动态的修改该值，调用 `channel.config().setAutoRead(true)` 方法，其实现在 `DefaultChannelConfig` 类中：

```
public class DefaultChannelConfig implements ChannelConfig {
    
    public ChannelConfig setAutoRead(boolean autoRead) {
        boolean oldAutoRead = AUTOREAD_UPDATER.getAndSet(this, autoRead ? 1 : 0) == 1;
        if (autoRead && !oldAutoRead) {
            channel.read();
        } else if (!autoRead && oldAutoRead) {
            autoReadCleared();
        }
        return this;
    }
}
```

设置该值时，会判断如果该值发生变动，会重新注册 read 事件监听，或者清除 read 事件监听。

### 可能会遇到的坑

虽然原理以及API都很简单，在实际使用中，如果使用不恰当，可能还是会踩到坑，这里笔者分享2个可能会遇到的问题。

**触发第一次读**

如果是通过 `option(ChannelOption.AUTO_READ, false)` 方式关闭 autoRead 的，那么需要在 channelActive 方法中主动调用一次 `channel.read()` 否则是不会触发任何读事件的。

**ByteToMessageDecoder**

使用该特性时，需要小心的与 `ByteToMessageDecoder` 搭配使用，主要有两个问题：

1. `ByteToMessageDecoder` 读事件的实现是 decoder 方法，真正的 channelRead 事件已经由该类帮你实现了，在 `ByteToMessageDecoder` 的实现中，可能已经从缓冲区中读取了数据，也就是说你的缓冲区中的数据已经从内核态被读取到用户态。如果你使用该特性的目的是为了防止OOM，这里可能需要注意一下。

2. 在该类的实现中 `channelReadComplete` 方法中会判断当前 channel 是否是 autoRead, 如果不是，会主动调用一次read。 这个特性导致我们设置的autoRead 完全失去意义。因此在实际应用中，也需要重载 `channelReadComplete` 方法。

## 结语

在生产者消费者系统中，为了匹配生产与消费的速率，流控尤为重要。在使用TCP协议的分布式系统中，由于TCP协议自带了一套流控机制，因此可以使用TCP协议协议中的缓冲区自动实现消费者向生产者的反压。Netty作为一款优秀的网络编程框架，也提供了在异步IO下实现流控的方法，其原理是通过向 epoll 的Flag或 JavaNIO的 SelectionKey 注册或移除 READ 标志实现的，了解其原理有助于我们更好的使用。

## 参考

1. [Apache Flink 进阶教程（七）：网络流控及反压剖析](https://ververica.cn/developers/advanced-tutorial-2-analysis-of-network-flow-control-and-back-pressure/)
2. [TCP滑动窗口协议](https://www.jianshu.com/p/07bd39becbfd)
3. [Netty API文档](https://netty.io/4.0/api/io/netty/channel/ChannelConfig.html)


