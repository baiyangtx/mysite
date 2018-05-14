---
title: docker.service start request repeated too quickly, refusing to start. 解决办法
date: 2018-1-8
---
docker.service start request repeated too quickly, refusing to start.
Apr 04 11:22:36 zyx-boostrap-fabric-test systemd[1]: Failed to start Docker Application Container Engine.


rm -f /var/lib/docker/
rm /etc/default/daemon.json # ONLY IN CASE YOU ARE NOT USING THIS AT ALL. Otherwise, check what works and what not, and try again!
mkdir /var/lib/docker/
chmod go-r /var/lib/docker/
systemctl restart docker

