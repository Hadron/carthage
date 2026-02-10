#!/bin/bash

apt-get update
apt-get install -y python3-{lmdb,hvac,mako,packaging,dateutil,pyvmomi,yaml,netifaces,pyroute2,requests,setuptools,sh}
./bin/carthage install_dependencies
