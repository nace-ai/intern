# YouTube Guardian — Drexathon submission (Team TheFirstTry)

> Don't be a slave to the algorithm. **Use YouTube as you want.**

**Source code:** https://github.com/Enchi-Chen/youtube-guardian

## Motivation

To boost clicks and engagement, content platforms' recommendation algorithms often push videos that
harm our mental well-being, keeping us hooked and spending more time on the platform.

YouTube Guardian uses **Drex**'s ability to absorb large amounts of text and make decisions quickly to
spot videos that should be filtered out, so we can focus on the valuable content YouTube has to offer.

## How it works

1. **Detect**: a Chrome (Manifest V3) extension detects the YouTube watch page and pauses playback.
2. **Extract**: a local Python (Flask) server fetches the video's transcript and title.
3. **Classify**: one Drex API call (`drex-v2.0`, `/v1/systemone`) answers four independent `noul` questions:
   sexual suggestiveness, graphic violence, sensationalism / clickbait, and learning & knowledge.
4. **Decide**: block or allow depending on the user's mode and a 0.7 score threshold.
5. **Show**: a blocked video gets an overlay with the scores and a **Watch anyway** button.

The transcript is the primary evidence and the title adds context. The Drex API key stays in a local
`.env` file and never reaches the browser.

## Two modes

- **Free mode**: block the categories you don't want; watch everything else.
- **Focus mode**: allow only the categories you need (e.g. learning & knowledge); block everything else.

In the demo the overlay is semi-transparent; in real use titles and thumbnails would be fully hidden to
minimize temptation.

## The challenge we couldn't beat (yet)

Drex classifies a long transcript in **under a second**. The bottleneck is YouTube: fetching transcripts for
many recommended videos triggers YouTube's protection and gets blocked, so sidebar and homepage
recommendations can't be classified reliably. For now Guardian works on the video the user opens.

## What's next

- Use screenshots, titles and other signals that don't trigger YouTube's protection.
- Block search results and sidebar recommendations as they load, before the user can click.
- Bring the same filtering to Reels on Instagram and Facebook.
