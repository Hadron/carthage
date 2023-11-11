VERSION != grep -Po 'version = "\K[^"]*' pyproject.toml

TESTENV = carthage-$(VERSION)-build-testenv

DIST = dist/carthage-$(VERSION).tar.gz

$(DIST):
	python3 -m build .

build: $(DIST)

test: build
	-mkdir tmp
	tar xf $(DIST) -C tmp/
	-podman run -d --name $(TESTENV) -v $$PWD/tmp:/carthage:ro docker.io/library/debian:bookworm sleep 3000
	-podman exec -it $(TESTENV) apt update
	-podman exec -it $(TESTENV) apt install -y git vim-tiny python3 python3-setuptools python3-pip python3-build
	-podman exec -it $(TESTENV) mkdir /build
	podman exec -it $(TESTENV) cp -r /carthage/ /build/
	podman exec -it $(TESTENV) python3 -m pip install --break-system-packages /build/carthage/carthage-$(VERSION)/
	podman exec -it $(TESTENV) bash

destroy:
	-podman stop -t 0 $(TESTENV)
	-podman rm $(TESTENV)

clean: destroy
	rm -rf carthage.egg-info dist tmp
	find . -name __pycache__ -exec rm -rf '{}' \; 2>/dev/null
