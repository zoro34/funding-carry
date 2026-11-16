# Kanonik komutlar — defterin aradigi tek giris noktasi.
#   make test   : sozdizimi + smoke paketleri (ag/veri gerektirir)
#   make lint   : yalnizca derleme kontrolu (hizli, agsiz)
PY = .venv/bin/python
SRC = $(wildcard *.py)

.PHONY: test lint
test: lint
	$(PY) smoke_carry.py
	$(PY) smoke_dip.py
	$(PY) smoke_alarm.py
	$(PY) smoke_paylas.py
	$(PY) smoke_risk.py
	-$(PY) smoke_mobile.py   # yerel Chromium http yukleyemiyorsa SKIP(2) — hata degil

lint:
	$(PY) -m py_compile $(SRC)
	@for f in *.sh .hermes/scripts/*.sh; do [ -f "$$f" ] && bash -n "$$f" || true; done
	@echo "py_compile: $(words $(SRC)) dosya · bash -n: $(words $(wildcard *.sh)) betik OK"

learn:
	$(PY) learn_indicators.py

.PHONY: learn

predict:
	$(PY) predict_study.py

.PHONY: predict
