VERSION != grep -Po 'version = "\K[^"]*' pyproject.toml

TESTENV = carthage-$(VERSION)-build-testenv

DIST = dist/carthage-$(VERSION).tar.gz
WHEEL = dist/carthage-$(VERSION)-py3-none-any.whl

$(DIST): dist
$(WHEEL): dist

dist:
	python3 -m build .

build: dist
test: $(DIST) $(WHEEL)
	cp --reflink=auto $(DIST) build_test/carthage.tar.gz
	-rm -f build_test/*whl
	cp --reflink=auto $(WHEEL) build_test
	podman build -t carthage:test build_test
	podman run -ti --rm --privileged carthage:test cd /carthage/*&&pytest-3 --carthage-config /authorized.yml tests/test_podman.py tests/test_dependency_injection.py tests/test_modeling.py tests/test_setup_tasks.py


destroy:
	-podman rmi carthage:test
	- rm -f build_test/*whl build_test/carthage.tar.gz

clean: destroy
	rm -rf carthage.egg-info dist tmp
	-find . -name __pycache__ -exec rm -rf '{}' \; 2>/dev/null

.PHONY : clean destroy test dist build

