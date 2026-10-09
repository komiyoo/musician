PY ?= python

.PHONY: all score render render-fallback mix samples clean analyze analyze-diff
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
	$(PY) -m musician.analyze --diff $(or $(REPO),.)
samples:                         ## download free sample packs into ./samples
	bash scripts/fetch_samples.sh all
clean:
	rm -rf build out midi/*.mid
