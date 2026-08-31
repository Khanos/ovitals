.PHONY: run test check install

run:
	./run

test:
	python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q vitals
	command -v desktop-file-validate >/dev/null && desktop-file-validate data/com.omarchy.OVitals.desktop || true

install:
	./install.sh
