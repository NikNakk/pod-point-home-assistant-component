.PHONY: setup setup-debian update-pip install-deps test develop

VENV := venv
PYTHON := $(VENV)/bin/python

setup-debian:
	sudo apt-get update
	sudo apt-get install python3-dev -y
	# Sometimes installing raumel.yaml fails and the fix is to purge gcc. No idea why
	# sudo apt-get purge gcc -y

setup: update-pip install-deps test
	echo "Done"

update-pip:
	python3 -m pip install -U pip
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install -U pip setuptools wheel

install-deps:
	$(PYTHON) -m pip install -r requirements.txt -r requirements_dev.txt -r requirements_test.txt

test:
	$(PYTHON) -m pytest \
		-vv \
		-qq \
		--timeout=9 \
		--durations=10 \
		--cov custom_components.pod_point \
		--cov-report term \
		--cov-report html \
		-o console_output_style=count \
		-p no:sugar \
		tests

develop:
	$(VENV)/bin/hass --config config --debug
