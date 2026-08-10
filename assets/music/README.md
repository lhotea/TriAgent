# Background music

Drop audio files here (`.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg`, `.flac`). One is
chosen per day, deterministically from the date, so a given day always gets the
same track and retries don't change it. An empty directory produces a silent
Reel, which still publishes.

Music only plays on **Reels**. Set the repo variable `POST_FORMAT=reel`.
Carousels and single images are silent — that is an Instagram rule, not a
limitation of this agent.

## Rights

**Use tracks you are licensed to use commercially.**

The Graph API cannot reach Instagram's in-app music library; that catalogue is
licensed for use inside the app only. Anything published through the API has its
audio baked into the file, so the licence has to be yours. Commercial music will
get the post muted or taken down.

Workable sources: music you own, Creative Commons tracks whose licence permits
commercial use *and* whose attribution terms you follow, or a subscription
library (Epidemic Sound, Artlist, Uppbeat) that covers social publishing.

Worth knowing: a Reel published via the API carries no trending-audio signal, so
it forgoes the reach boost that picking a sound inside the app would give. If
audio is mainly a reach play, posting by hand may beat automating it.
