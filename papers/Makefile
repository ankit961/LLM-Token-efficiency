.PHONY: all assets measurement runtime clean

PYTHON ?= python3
LATEXMK ?= latexmk

all: assets measurement runtime

assets:
	$(PYTHON) scripts/generate_assets.py

measurement: assets
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error -cd measurement/main.tex
	mkdir -p dist
	cp measurement/main.pdf dist/context-residency-measurement.pdf

runtime: assets
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error -cd runtime/main.tex
	mkdir -p dist
	cp runtime/main.pdf dist/contextruntime-systems.pdf

clean:
	$(LATEXMK) -C -cd measurement/main.tex
	$(LATEXMK) -C -cd runtime/main.tex
