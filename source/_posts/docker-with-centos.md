---
title: Centos 7.2 下Docker安装启动失败
date: 2017-7-15
---

最近客户反应我小组维护的一个Dockers容器在Centos 7.2 下无法启动，故准备在测试环境重现一下，环境搭建过程中也遇到坑

无论是通过 yum install docker 安装还是通过  curl -sSL https://get.docker.com | sh 安装均无法启动，报错日志为:

```
Jul 14 13:53:22 hamysql-test-zyx.novalocal dockerd[52512]: time="2017-07-14T13:53:22.901635424+08:00" level=info msg="libcontainerd: new containerd process, pid: 52515"
Jul 14 13:53:23 hamysql-test-zyx.novalocal dockerd[52512]: time="2017-07-14T13:53:23.910526284+08:00" level=info msg="[graphdriver] using prior storage driver: overlay"
Jul 14 13:53:23 hamysql-test-zyx.novalocal dockerd[52512]: time="2017-07-14T13:53:23.918338377+08:00" level=info msg="Graph migration to content-addressability took 0.00 seconds"
Jul 14 13:53:23 hamysql-test-zyx.novalocal dockerd[52512]: time="2017-07-14T13:53:23.918623779+08:00" level=warning msg="mountpoint for pids not found"
Jul 14 13:53:23 hamysql-test-zyx.novalocal dockerd[52512]: time="2017-07-14T13:53:23.918878700+08:00" level=info msg="Loading containers: start."
Jul 14 13:53:23 hamysql-test-zyx.novalocal dockerd[52512]: Error starting daemon: Error initializing network controller: list bridge addresses failed: no available network
Jul 14 13:53:23 hamysql-test-zyx.novalocal systemd[1]: docker.service: main process exited, code=exited, status=1/FAILURE
Jul 14 13:53:23 hamysql-test-zyx.novalocal systemd[1]: Failed to start Docker Application Container Engine.
-- Subject: Unit docker.service has failed
-- Defined-By: systemd
-- Support: http://lists.freedesktop.org/mailman/listinfo/systemd-devel
-- 
-- Unit docker.service has failed.
-- 
-- The result is failed.
```

日志中有一句：Error starting daemon: Error initializing network controller：no available network 看上去应该是和网络有关，docker安装后会多出一个 docker0 的接口，是不是这个接口没有创建成功呢？通过 ip a 一看果然没有。后来在github上找到了相关讨论 [issues](https://github.com/moby/moby/issues/31546) 通过以下方法手工为docker创建接口即可：

    sudo brctl addbr docker0
    sudo ip addr add 192.168.42.1/24 dev docker0
    sudo ip link set dev docker0 up
    ip addr show docker0
    sudo systemctl restart docker
    sudo iptables -t nat -L -n    
    
如果没有 brctl 可以通过 `yum install bridge-utils` 命令安装。 [issues](https://github.com/moby/moby/issues/31546) 中提到可能产生的原因是宿主机上因为开了VPN的原因，当时我测试的机器虽然没有连着VPN但是由于是在云环境中创建的虚拟机，的确有多个网关接口，但是我后来在只有一个网关接口的虚拟机上测试，还是会有同样的问题。

docker安装完成后我就开始测试镜像无法启动的问题，通过docker run命令的确无法启动，报错为：

![image](/postimgs/docker-with-centos/overlay-err.png)

最终定位到原因是docker 存储格式的问题。在centos 下 docker支持的存储格式有两种，一种是 `overlayfs` 一种是 `devicemapper` overlayfx 是centos系统推出的一种更高效的存储格式，但是需要至少在内核版本 3.18 以上才支持，docker安装完成后默认设置了存储格式为 overlayfs 导致启动失败，解决方法也很简单，在 `/etc/docker/daemon.json` 中设置

```
{
  "storage-driver": "devicemapper"
}
```
即可。[Use the Device Mapper storage driver](https://docs.docker.com/engine/userguide/storagedriver/device-mapper-driver/#configure-loop-lvm-mode-for-testing)


