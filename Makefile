.PHONY: all sentences coref merge events triplets clean clean-events clean-triplets clean-derived help

PYTHON := pdm run python

TEXT := bohemia.txt
SENTENCES := bohemia_sentences.jsonl
COREF := bohemia_coref.jsonl
ENTITIES := bohemia_entities.jsonl
MENTIONS := bohemia_mentions.jsonl
EVENTS := bohemia_events.jsonl
MOMENTS := bohemia_moments.jsonl
TRIPLETS := bohemia_triplets.jsonl

EVENTS_PROGRESS := .bohemia_events_progress.json
TRIPLETS_PROGRESS := .bohemia_triplets_progress.json

all: triplets

help:
	@echo "Targets:"
	@echo "  make sentences       # build $(SENTENCES) from $(TEXT)"
	@echo "  make coref           # build $(COREF) from $(SENTENCES)"
	@echo "  make merge           # build $(ENTITIES) and $(MENTIONS) from $(COREF)"
	@echo "  make events          # build $(EVENTS) and $(MOMENTS)"
	@echo "  make triplets        # build $(TRIPLETS)"
	@echo "  make all             # same as triplets"
	@echo "  make clean-events    # remove event outputs and resume file"
	@echo "  make clean-triplets  # remove triplet outputs and resume file"
	@echo "  make clean-derived   # remove all generated pipeline artifacts"
	@echo "  make clean           # alias for clean-derived"

sentences: $(SENTENCES)

$(SENTENCES): $(TEXT) src/sentencize.py
	$(PYTHON) src/sentencize.py --input $(TEXT) --output $(SENTENCES)

coref: $(COREF)

$(COREF): $(SENTENCES) src/coref.py
	$(PYTHON) src/coref.py --input $(SENTENCES) --output $(COREF)

merge: $(ENTITIES) $(MENTIONS)

$(ENTITIES) $(MENTIONS): $(COREF) src/merge.py
	$(PYTHON) src/merge.py --coref $(COREF) --entities $(ENTITIES) --mentions $(MENTIONS)

events: $(EVENTS) $(MOMENTS)

$(EVENTS) $(MOMENTS): $(SENTENCES) $(ENTITIES) src/events.py
	$(PYTHON) src/events.py --sentences $(SENTENCES) --entities $(ENTITIES) --events $(EVENTS) --moments $(MOMENTS)

triplets: $(TRIPLETS)

$(TRIPLETS): $(SENTENCES) $(ENTITIES) $(EVENTS) $(MOMENTS) src/triplets.py
	$(PYTHON) src/triplets.py --sentences $(SENTENCES) --entities $(ENTITIES) --events $(EVENTS) --moments $(MOMENTS) --output $(TRIPLETS)

clean-events:
	rm -f $(EVENTS) $(MOMENTS) $(EVENTS_PROGRESS)

clean-triplets:
	rm -f $(TRIPLETS) $(TRIPLETS_PROGRESS)

clean-derived:
	rm -f $(SENTENCES) $(COREF) $(ENTITIES) $(MENTIONS) $(EVENTS) $(MOMENTS) $(TRIPLETS) \
		$(EVENTS_PROGRESS) $(TRIPLETS_PROGRESS)

clean: clean-derived
