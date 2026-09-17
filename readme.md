# Fediverse Deepfake-Text Corpus

This repository contains a large-scale deepfake-text corpus from the Fediverse. The
corpus contains human-written text (HWT, label `0`) collected from pre-LLM
Fediverse snapshots and AI-generated text (AIGT, label `1`) generated based on
that HWT corpus.

## Corpus files

The corpus is provided as nine standalone JSON Lines shards:

| Files | Records |
| --- | ---: |
| `corpus/corpus_01.jsonl` through `corpus/corpus_09.jsonl` | 1,003,993 |

Each line represents one post object. Each shard is below 45 MB and
ends at a JSONL record boundary. The corpus contains 523,048 HWT records and
480,945 AIGT records across 263 Mastodon communities.

## Record schema

Each line is one JSON object. The public corpus uses these fields:

| Field | Type | Description |
| --- | --- | --- |
| `text` | string | Cleaned post text. |
| `label` | integer | `0` for HWT and `1` for AIGT. |
| `community_id` | string | Mastodon instance/community domain. |
| `is_reply` | boolean | Whether the post is a reply. |
| `language` | string | Language metadata released by the collection source, or `unknown`. |
| `post_id` | string or null | Post identifier for HWT records (hash value). |
| `original_id` | string or null | HWT source identifier associated with AIGT records (hash value). |
| `generation_method` | string or null | AIGT generation strategy. |
| `llm_model` | string or null | Target LLM selected for generation. |

### Note
- The public `post_id` and `original_id` values are lowercase SHA-256 hex
digests of the original identifier for user privacy and anonymity. The `original_id` to `post_id` linkage
between AIGT and HWT records is preserved.

- The `language` field is collection-source metadata, not a newly inferred
language label. The raw release contains 97 values including `unknown` and
source-specific variants; excluding `unknown` and grouping variants such as
`ja-IM` with `ja` yields the 89 reported language categories used in the
manuscript. For downstream language-aware analysis, we suggest using a
language identification model such as FastText instead of directly utilizing the source metadata.

## AIGT generation

The generation pipeline uses seven representative LLM families: GPT-4-Turbo,
GPT-4o-mini, Claude-3.5-Sonnet, Claude-Sonnet-4, Gemini-2.0-Flash,
Qwen-2.5-32B-Instruct, and LLaMA3-8B-Instruct. The recorded strategies are
`polish`, `complete`, and `3-iteration paraphrase`.

## Instance metadata

The `instance_metadata/instance_metadata_*.jsonl` files contain one JSON
object per line with crawl results for Mastodon instances. These files provide
community context and are separate from the post JSONL shards.

`instance_metadata/metadata_errors.csv` records crawl failures. Because
ActivityPub instances can freely connect or disconnect, an unavailable
instance may become reachable on a later crawl; retries must follow applicable
platform policies.

## Instance-level statistics and distribution

### Top-10 Instances Statistics

| Instance | Posts | HWT | AIGT | HWT % | AIGT % |
|---|---:|---:|---:|---:|---:|
| mastodon.social | 198,827 | 109,075 | 89,752 | 54.86 | 45.14 |
| pawoo.net | 153,665 | 77,780 | 75,885 | 50.62 | 49.38 |
| mstdn.maud.io | 55,918 | 28,306 | 27,612 | 50.62 | 49.38 |
| chaosphere.hostdon.jp | 48,292 | 24,172 | 24,120 | 50.05 | 49.95 |
| imastodon.net | 44,370 | 22,305 | 22,065 | 50.27 | 49.73 |
| mamot.fr | 38,991 | 20,513 | 18,478 | 52.61 | 47.39 |
| rewa.mobi | 29,088 | 15,774 | 13,314 | 54.23 | 45.77 |
| eletusk.club | 24,218 | 12,223 | 11,995 | 50.47 | 49.53 |
| mstdn.guru | 23,338 | 11,725 | 11,613 | 50.24 | 49.76 |
| pokemon.mastportal.info | 16,085 | 8,160 | 7,925 | 50.73 | 49.27 |

### Post-volume distribution across instances

![Post-volume distribution across instances](post_volume_distribution.png)

*Figure: Distribution of post volumes across Mastodon instances. The x-axis is shown on a logarithmic scale.*

### Example

The examples below illustrate how community context and paired HWT/AIGT text
appear across representative instances. Ellipses indicate shortened examples.

| Instance | Description and community context | HWT examples | AIGT examples |
|---|---|---|---|
| **mastodon.social**<br>109,075 posts | The original server operated by the Mastodon gGmbH non-profit.<br><br>*Rules: No misinformation, no harassment, no violence incitement...* | Annabel found some Halloween-ready lighting.<br><br>I've also applied to put up a shop on Designed by Humans, since I think...<br><br>okay it's food truck o'clock I guess... | Yo, peep the Halloween swag Annabel just found!<br><br>I've also applied to open a shop on Designed by Humans. Their shipping rates...<br><br>so I better eat something quick before the show starts... |
| **pawoo.net**<br>77,780 posts | Pawoo, a Mastodon instance operated by The Social Coop Limited...<br><br>*Rules: No rules.* | i could listen to the 100 poets i replay for days<br><br>[Update] IconTweak 1.0.1 - Show App Versions in Menu<br><br>RT: Oh, I just love #Japan! #Tokyo #subway | I could listen to those 100 poets over and over again, replaying for days!<br><br>[Update] IconTweak 1.0.1 is here! Now you can see app versions directly in the menu. Super handy, right?<br><br>RT: Oh, I just love #Japan! The culture, the food, and the scenery... |
| **mstdn.maud.io**<br>28,306 posts | The place to express you more freely.<br><br>*Rules: Comply with law, no disruption, check updates regularly...* | The Pirate Bay was recently down for over a week due to a DDoS attack<br><br>What is CMAF? Threat or Opportunity?<br><br>GitHub - neuecc/Utf8Json: Definitely Fastest and Zero Allocation JSON Serializer... | The Pirate Bay was offline for more than a week recently because of a DDoS attack.<br><br>Is CMAF a chance or a risk?<br><br>Definitely a game-changer if you're looking for performance. The speed is unreal, and zero allocations mean... |
| **mastodon.art**<br>5,397 posts | Your friendly home on the Fediverse for all things creative.<br><br>*Rules: No AI or NFTs, credit + commentary required, respect user boundaries...* | Once upon a time I started this. I think I'll continue it, now. #MastoArt<br><br>Compliment my costume and I'll give you more candy<br><br>second october patreon reward teaser #digitalart... | Been sitting on this project for a while now...<br><br>Compliment my costume, and I'll give you some extra candy!<br><br>second october patreon reward teaser #digitalsketch... |
