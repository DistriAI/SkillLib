# Skill search

`find_skills` ranks the leading skills in the catalog and returns a few routing cards. The shelves below are how the catalog is organized. The ranker that decides the order is Okapi BM25 over those cards and the skill text on disk. On the 87 SkillsBench tasks, that ranker reaches Hit@1 0.6897 and Recall@10 0.7292. The previous ranker, which added a fixed weight for every shared word, reached Hit@1 0.2874 and Recall@10 0.3398. Plain BM25 over the same catalog text reaches Hit@1 0.6552 and Recall@10 0.6560.

## Shelves

The searchable unit is one routing card, one per leading skill (20,178 cards). Larger shelves narrow or annotate that card. They are not a second retrieval stage.

| Shelf | What it contributes to search |
|---|---|
| Domain | Optional filter. `domain="science"` drops every card whose domains do not include science. Thirteen domains. |
| Scenario | The shelf a card is filed under for browsing. Search does not score the scenario name on its own. |
| Task family | Method skills in one domain share a family name and a one-sentence definition. Those words are extra evidence on the card. About 394 families. |
| Product channel | A skill tied to a named product. When every token of that product name appears in the task, the card gains a fixed bonus. About 964 channels. |
| Routing card | Name, aliases, description, positive scope, negative scope, and preconditions. This is the record search scores. |
| Skill text | The on-disk `SKILL.md` for the card's leading source, capped at 2,500 characters. Its word counts join the card before scoring. |

A combined card still has one id. Search reads one skill file for it: the leader when the card has one, otherwise the first source that has a path. `load_skill` is what opens the procedure, including the other peers.

`follows` links are applied after ranking. A pair is returned when the task matches both ends. The link does not change either card's score.

## What is scored

Each card becomes a bag of tokens. A token is a run of letters and digits, lowercased, with a trailing `s` removed when the word is longer than four characters. `a`, `an`, `the`, `on`, `for`, `to`, `of`, `and`, `with`, `from`, `via`, `or`, `in`, and `by` are dropped.

Field weights are term frequencies, not a sum that ignores how common the word is. When the same token occurs in more than one field, the highest weight is kept, and the skill text adds its own count on top.

| Evidence | Term frequency |
|---|---|
| Name | 6 |
| Each alias | 6 |
| Description | 3 |
| Each positive-scope phrase | 3 |
| Family name | 4 |
| Each token in the family name and definition | +2 |
| Skill text | one per occurrence in the first 2,500 characters |

The skill text is read from the checkout's `cache/` and then from the `cache` path in `skilllib.json`. A missing file is not downloaded during ranking. The first search in a process reads those files and builds a postings list. Later searches in that process reuse the postings. A repository file listing is walked once, kept in memory, and written under `.cache/finder_repos/` so the next process does not walk that tree again while the checkout directory's mtime is unchanged. Term counts are written to `.cache/finder_terms.jsonl`, and the next process reuses a card's counts while `routing_cards.jsonl` is unchanged and that skill file's size and mtime match. A card whose description or positive scope is empty can still be filled from its file before the final order. Only that card's postings are updated.

## The mechanism that raised retrieval

The old ranker added the field weight of every token that also appeared in the task. A long description full of ordinary words (`use`, `data`, `file`, `this`) outscored a short card whose name was the skill the task needed. `use` alone sits on about two thirds of the cards, and each hit still added 3. The cutoff at 5 did not select: on a typical SkillsBench task more than half the catalog cleared it.

The current ranker keeps those field weights and scores them with Okapi BM25 (`k1 = 1.5`, `b = 0.75`). For a query token that occurs `tf` times on a card:

```
idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_length))
```

`idf` is `log(1 + (N - df + 0.5) / (df + 0.5))`, where `df` is the number of cards that contain the token and `N` is the number of cards. A token on most cards adds little. A card much longer than the average is discounted, so a verbose description no longer wins by matching more ordinary words.

Two pieces do the work:

- Inverse document frequency and length normalization, applied to the field weights above. This is what stops a generic card such as `get-available-resources` from beating a specific skill. Its description tells the agent to use it at the start of any heavy scientific task, so the old sum gave it the highest score on many unrelated tasks.
- The skill text. The card's description often does not contain the words the task uses for the procedure. Those words are in `SKILL.md`. Adding that text is why this ranker also passes plain BM25 over `name | description | body`. The name weight of 6 still lets the skill's own name outrank a single mention in a body.

On top of that lexical score, an exact product-name match adds 25. A negative-scope phrase that covers the task subtracts 12 when at least 60% of its tokens occur in the task, and 4 when at least two tokens overlap. Each precondition the caller did not pass in `constraints` subtracts 3. A card with no remaining score of at least 1 is omitted, so a task that shares nothing with the catalog returns no cards.

## What search does not do

Search does not walk domain, then family, then card as three filters. The domain argument is the only hard shelf filter. Family and product evidence are token weights and one bonus inside the same score. Embeddings, a reranker, and a chat model are not part of `find_skills`.
