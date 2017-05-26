.PHONY: clean tag test pypi

version := $(shell python3 setup.py --version)

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
	find . -name ".DS_Store" -delete
	rm -rf ftl.egg-info

tag:
	git tag $(version)

# Testing

test:
	tox --recreate

# Distribution

pypi:
	rm -rf dist/*
	python3 setup.py sdist
	python3 setup.py bdist_wheel
	twine upload dist/*
