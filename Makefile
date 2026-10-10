# Dependencies are managed by uv (pyproject.toml + uv.lock). `make sync` once, then any target;
# `uv run make <target>` also works. Without uv, falls back to .venv/bin/python or python3.
UV := $(shell command -v uv 2>/dev/null)
PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
REQ_HEADER := \# 由 uv.lock 自动生成（make requirements），供不用 uv 的 pip 用户；请改 pyproject.toml，不要手改本文件。

.PHONY: all score render render-fallback mix samples clean analyze analyze-diff demo-analyze web web-deps smoke-image smoke-markov test \
        sync lock requirements serve
all: score render mix            ## full pipeline (uses Surge/sfizz/samples if present)

score:                           ## 1. compose -> midi/*.mid
	$(PY) -m src.score.write_score
render:                          ## 2. MIDI -> build/stems/*.wav (Surge XT + sfizz, auto fallback)
	$(PY) -m src.render.render_all
render-fallback:                 ## 2'. force the numpy sketch synth for every part
	$(PY) -m src.render.render_all --fallback
mix:                             ## 3. loudness align + FX + master -> out/final.wav
	$(PY) -m src.mix.mix
analyze:                         ## code structure of REPO (default: ./src) -> midi/analyze/*.mid -> out/analyze.wav
	$(PY) -m musician.analyze $(or $(REPO),src)
analyze-diff:                    ## git diff of REPO (default: .) -> midi/analyze/diff/*.mid -> out/analyze_diff.wav
	$(PY) -m musician.analyze --diff --heat $(or $(REPO),.)
demo-analyze:                    ## demo: this project's own src/ -> out/analyze.wav (+ .mp3) + out/analysis.json
	$(PY) -m musician.analyze src --summary out/analysis.json $(if $(FALLBACK),--fallback,)
web:                             ## web UI at http://127.0.0.1:$(or $(PORT),8765)/  (FALLBACK=1 = 草稿音色)
	$(PY) -m src.web.app --port $(or $(PORT),8765) $(if $(FALLBACK),--fallback,)
smoke-image:                     ## image → music smoke test (tiny generated PNG → knobs → spec; URL=http://... also hits the API)
	$(PY) scripts/smoke_image.py $(if $(URL),--url $(URL) --preview,)
smoke-markov:                    ## Markov chain smoke: --fallback server → preview graph nodes/edges → 变奏 → 致艾丽丝；UI=1 adds headless Chrome (playwright via uv --with)
ifdef UI
	uv run --with playwright python scripts/smoke_markov.py --ui $(if $(URL),--url $(URL),)
else
	$(PY) scripts/smoke_markov.py $(if $(URL),--url $(URL),)
endif
test:                            ## unit tests (vision JSON parse / fusion / cache / no-key fallback; offline, HTTP mocked)
	$(PY) -m unittest discover -s tests -v
serve: web                       ## alias of `make web`
web-deps:                        ## install FastAPI + uvicorn for the web UI (uv sync --extra web; pip fallback)
ifdef UV
	uv sync --extra web
else
	$(PY) -m pip install -r requirements-web.txt
endif
sync:                            ## uv: create .venv + install project & all deps (incl. web extra) from uv.lock
	uv sync --extra web
lock:                            ## uv: re-resolve uv.lock after editing pyproject.toml dependencies
	uv lock
requirements: lock               ## regenerate requirements*.txt (pinned, for pip users) from uv.lock
	{ echo "$(REQ_HEADER)"; uv export --frozen --format requirements.txt --no-hashes --no-emit-project --no-header; } > requirements.txt
	{ echo "$(REQ_HEADER)"; uv export --frozen --format requirements.txt --no-hashes --no-emit-project --no-header --extra web; } > requirements-web.txt
samples:                         ## download free sample packs into ./samples
	bash scripts/fetch_samples.sh all
clean:
	rm -rf build out midi/*.mid
