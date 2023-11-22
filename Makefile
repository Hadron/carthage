VERSION != grep -Po 'version = "\K[^"]*' pyproject.toml

TESTENV = carthage-$(VERSION)-build-testenv

DIST = dist/carthage-$(VERSION).tar.gz
WHEEL = dist/carthage-$(VERSION)-py3-none-any.whl

dist: clean $(WHEEL) $(DIST)

$(DIST) $(WHEEL):
	python3 -m build .

build: dist
test_env: $(DIST) $(WHEEL)
	cp --reflink=auto $(DIST) build_test/carthage.tar.gz
	-rm -f build_test/*whl
	cp --reflink=auto $(WHEEL) build_test
	podman build -t carthage:test build_test

test: test_env
	podman run -ti --rm --privileged carthage:test sh -c 'set -e&&cd /carthage/*&&pytest-3 --carthage-config=/authorized.yml -k "not no_rootless and not test_pki" tests'


destroy:
	-podman rmi carthage:test
	- rm -f build_test/*whl build_test/carthage.tar.gz

clean: destroy
	rm -rf carthage.egg-info dist tmp
	-find . -name __pycache__ -exec rm -rf '{}' \; 2>/dev/null

.PHONY : clean destroy test dist build test_env

