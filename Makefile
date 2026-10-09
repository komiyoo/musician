PY ?= python

.PHONY: all score render render-fallback mix samples clean
all: score render mix            ## full pipeline (uses Surge/sfizz/samples if present)

score:                           ## 1. compose -> midi/*.mid
	$(PY) -m src.score.write_score
render:                          ## 2. MIDI -> build/stems/*.wav (Surge XT + sfizz, auto fallback)
	$(PY) -m src.render.render_all
render-fallback:                 ## 2'. force the numpy sketch synth for every part
	$(PY) -m src.render.render_all --fallback
mix:                             ## 3. loudness align + FX + master -> out/final.wav
	$(PY) -m src.mix.mix
samples:                         ## download free sample packs into ./samples
	bash scripts/fetch_samples.sh all
clean:
	rm -rf build out midi/*.mid
