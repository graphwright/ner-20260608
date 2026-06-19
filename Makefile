.PHONY: all sentences coref merge events triplets fresh-events fresh-triplets clean clean-events clean-triplets clean-derived help

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

SENT_MODEL ?= sonnet-4.6
COREF_MODEL ?= sonnet-4.6
EVENTS_MODEL ?= sonnet-4.6
TRIPLETS_MODEL ?= sonnet-4.6

OLLAMA ?= http://192.168.1.162:11434
EVENTS_OLLAMA ?= $(OLLAMA)
TRIPLETS_OLLAMA ?= $(OLLAMA)
ANTHROPIC ?= 0

ifeq ($(ANTHROPIC),1)
SENT_LLM_FLAGS := --model $(SENT_MODEL) --anthropic
COREF_LLM_FLAGS := --model $(COREF_MODEL) --anthropic
EVENTS_LLM_FLAGS := --model $(EVENTS_MODEL)
TRIPLETS_LLM_FLAGS := --model $(TRIPLETS_MODEL) --anthropic
else
SENT_LLM_FLAGS := --model $(SENT_MODEL) --ollama $(OLLAMA)
COREF_LLM_FLAGS := --model $(COREF_MODEL) --ollama $(OLLAMA)
EVENTS_LLM_FLAGS := --model $(EVENTS_MODEL)
TRIPLETS_LLM_FLAGS := --model $(TRIPLETS_MODEL) --ollama $(TRIPLETS_OLLAMA)
endif

SENT_GUTENBERG ?= 0
COREF_CHUNK_SIZE ?= 20
COREF_OVERLAP ?= 3
TRIPLETS_CHUNK_SIZE ?= 15
TRIPLETS_OVERLAP ?= 3
TRIPLETS_EVENT_WINDOW ?= 15

ifeq ($(SENT_GUTENBERG),1)
SENT_GUTENBERG_FLAG := --gutenberg
else
SENT_GUTENBERG_FLAG :=
endif

all: triplets

help:
	@echo "Targets:"
	@echo "  make sentences       # build $(SENTENCES) from $(TEXT)"
	@echo "  make coref           # build $(COREF) from $(SENTENCES)"
	@echo "  make merge           # build $(ENTITIES) and $(MENTIONS) from $(COREF)"
	@echo "  make events          # build $(EVENTS) and $(MOMENTS)"
	@echo "  make triplets        # build $(TRIPLETS)"
	@echo "  make fresh-events    # clear event outputs/progress, then rebuild them"
	@echo "  make fresh-triplets  # clear triplet outputs/progress, then rebuild them"
	@echo "  make all             # same as triplets"
	@echo "  make clean-events    # remove event outputs and resume file"
	@echo "  make clean-triplets  # remove triplet outputs and resume file"
	@echo "  make clean-derived   # remove all generated pipeline artifacts"
	@echo "  make clean           # alias for clean-derived"
	@echo ""
	@echo "Common overrides:"
	@echo "  make sentences SENT_GUTENBERG=1"
	@echo "  make coref COREF_CHUNK_SIZE=25 COREF_OVERLAP=5"
	@echo "  make triplets TRIPLETS_EVENT_WINDOW=10 TRIPLETS_CHUNK_SIZE=12"
	@echo "  make all OLLAMA=http://host:11434"
	@echo "  ANTHROPIC=1 make all"

sentences: $(SENTENCES)

$(SENTENCES): $(TEXT) src/sentencize.py
	$(PYTHON) src/sentencize.py --input $(TEXT) --output $(SENTENCES) $(SENT_LLM_FLAGS) $(SENT_GUTENBERG_FLAG)

coref: $(COREF)

$(COREF): $(SENTENCES) src/coref.py
	$(PYTHON) src/coref.py --input $(SENTENCES) --output $(COREF) $(COREF_LLM_FLAGS) --chunk-size $(COREF_CHUNK_SIZE) --overlap $(COREF_OVERLAP)

merge: $(ENTITIES) $(MENTIONS)

$(ENTITIES) $(MENTIONS): $(COREF) src/merge.py
	$(PYTHON) src/merge.py --coref $(COREF) --entities $(ENTITIES) --mentions $(MENTIONS)

events: $(EVENTS) $(MOMENTS)

$(EVENTS) $(MOMENTS): $(SENTENCES) $(ENTITIES) src/events.py
	$(PYTHON) src/events.py --sentences $(SENTENCES) --entities $(ENTITIES) --events $(EVENTS) --moments $(MOMENTS) $(EVENTS_LLM_FLAGS)

triplets: $(TRIPLETS)

$(TRIPLETS): $(SENTENCES) $(ENTITIES) $(EVENTS) $(MOMENTS) src/triplets.py
	$(PYTHON) src/triplets.py --sentences $(SENTENCES) --entities $(ENTITIES) --events $(EVENTS) --moments $(MOMENTS) --output $(TRIPLETS) $(TRIPLETS_LLM_FLAGS) --chunk-size $(TRIPLETS_CHUNK_SIZE) --overlap $(TRIPLETS_OVERLAP) --event-window $(TRIPLETS_EVENT_WINDOW)

fresh-events: clean-events events

fresh-triplets: clean-triplets triplets

clean-events:
	rm -f $(EVENTS) $(MOMENTS) $(EVENTS_PROGRESS)

clean-triplets:
	rm -f $(TRIPLETS) $(TRIPLETS_PROGRESS)

clean-derived:
	rm -f $(SENTENCES) $(COREF) $(ENTITIES) $(MENTIONS) $(EVENTS) $(MOMENTS) $(TRIPLETS) \
		$(EVENTS_PROGRESS) $(TRIPLETS_PROGRESS)

clean: clean-derived
