---
title: ssh反向隧道-借助公网服务器访问内网机器
date: 2016-9-30
---

# 从外网到内网的访问

最近做学校的项目的时候遇到这样一种需求，项目需要在不同的学校的内网中部署服务器，并且希望可以对服务进行远程运维。校园网结构极其复杂，而且无法获取校园网关的权限做端口配置与转发，因此需要一种方法建立从外部网络访问内部网络的方法。鉴于项目中服务器是采用了Windows系统，于是直接采用了商业软件TeamViewer解决的。但是该软件的服务器位于国外，而且极不稳定并且没有提供Linux下的版本。由于项目后期可能会使用Linux，而且我们在公网有一台云服务器且部署在学校内部的服务器和办公区均可以无障碍的访问该外网下的云服务器，因此就产生了采用该服务器当中继访问内部网络的想法。

![image](/postimgs/ssh-tunnel/ssh-tunnel.png)

具体的网络示意如图所示，其中绿色箭头的是设备实际可以的访问方向，而橘色虚线箭头表示希望建立访问的通路，即希望借助两个内网设备都可以访问到的外网服务器建立从PC-A 到 Server-Target的访问。


# SSH反向隧道

SSH大家都很熟悉，作为一种安全的传输协议，是目前linux下远程登录主机最常用的方法。不过linux下常用的ssh还有建立隧道与转发的功能。 可以通过 `ssh -L` 命令建立本地转发以及通过 `ssh -R` 命令建立远程转发。
这里采用的方式就是通过远程转发建立反向连接。

直接上命令。 在需要被访问的内网主机 Server-Target上执行

    ssh -fNg -R port:localhost:22 username@Server-Public
上述命令中的 username是登录到服务器 Server-Public上的用户  

参数 `-R` 表示建立ssh反向转发。服务器Server-Public 上的ssh服务端将会监听 `port` 端口并将该端口上的数据通过反向转发给 Server-Target， Server-Target 再将数据转发到 localhost:22 这个端口，也就是Server-Target的ssh登录端口。  
 
参数 `f` 表示ssh反向转发隧道会一直在后台运行。

参数 `N` 表示该反向隧道并不会执行命令，只是单纯的数据转发。

参数 `g` 是 PC-A 可以登录到Server-Target服务器的关键。因为虽然反向转发建立成功了，Server-Public ssh服务端已经在监听指定的 port 端口，但是默认绑定的ip地址与端口号是 `127.0.0.1:port` 也就是说只监听来自本地的链接。 这个时候在Server-Public上通过ssh连接本地port端口可以登录到 Server-Target 上，但是其他主机是无法访问这个 port端口的。 必须通过参数 `g` 将ssh服务监听的端口绑定到 `0.0.0.0:port` 这个地址上。 **需要注意的是ssh默认配置中是禁止远程主机访问转发端口的，因此只加上该参数还不行，需要修改配置ssh文件**  
打开Server-Public 上的ssh配置文件，默认是在 /etc/ssh/sshd_config 。
在配置文件的最后加上或开启 `GatewayPorts yes`  然后重启 sshd 服务即可。


在主机 PC-A上，通过ssh客户端连接 Server-Public 上刚才配置的端口，可以看到成功的登录到了主机 Server-Target

