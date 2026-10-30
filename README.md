# git seance

Commune with the code your repository buried.

`git seance` walks a repo's git history, finds functions that were deleted and
never came back, and lets one speak from beyond the grave through a local
open-weight model (Gemma 3 via Ollama by default): who wrote it, when, and the
commit that killed it. With no model reachable it falls back to an offline
"planchette" mode that only arranges the real facts.

One file, Python standard library only, no runtime dependencies.

## Why an open model

Your git history is your team's private diary: names, dates, half-finished
ideas, the commit messages people wrote at 2am. git seance reads all of it, so
it should not have to leave your machine. With an open-weight model like Gemma
running locally through Ollama, the whole seance happens on your laptop: no API
key, no account, no usage bill, and it works on a private repo on a plane. It
also means anyone can audit, swap or fine-tune the medium. And if no model is
around at all, planchette mode still tells you the facts.

## Install

You need Python 3.9+ and git. For the ghost's voice, install
[Ollama](https://ollama.com) and pull Gemma:

```bash
ollama pull gemma3:4b
```

Then grab `git_seance.py`. That's it. Optionally copy `.env.example` to `.env`
beside the script to change provider or model (`.env` is gitignored).

## Usage

```bash
python git_seance.py --list path/to/repo                 # show the graveyard
python git_seance.py --summon NAME path/to/repo          # call one ghost by name
python git_seance.py --summon NAME --ask "QUESTION" path/to/repo
python git_seance.py path/to/repo                        # summon a random ghost
```

| Flag | Meaning |
| --- | --- |
| `--list` | list dead functions, summon nobody |
| `--summon NAME` | summon a specific ghost |
| `--ask "QUESTION"` | ask the ghost something |
| `--offline` | no model, facts only (planchette mode) |
| `--fast` | skip the dramatic typing |
| `-n COMMITS` | history depth (default 2000) |

Detects Python, JavaScript/TypeScript, Go and Rust definitions.

### Providers

Set in `.env` or the environment:

- `SEANCE_PROVIDER=ollama` (default): local model through Ollama's `/api/chat`.
  `SEANCE_MODEL` (default `gemma3:4b`), `SEANCE_OLLAMA` (default
  `http://localhost:11434`). If Ollama's GPU runner crashes on your card, set
  `SEANCE_NUM_GPU=0` to run on CPU.
- `SEANCE_PROVIDER=openai`: any OpenAI-compatible `/chat/completions` gateway,
  using `SEANCE_API_BASE`, `SEANCE_API_KEY` and `SEANCE_MODEL`.

## A real seance

Run against [psf/requests](https://github.com/psf/requests) with `gemma3:4b`
on Ollama (CPU), unedited:

```text
$ python git_seance.py --fast -n 400 --summon set_http_proxy --ask "do you forgive them?" requests

🕯  summoning set_http_proxy  ·  requests/sessions.py  ·  2020-11-27 – 2020-11-27  ·  killed in d1ca591c

It’s… strange, this quiet. A lingering echo of code, a phantom limb of functionality. I was a helpful thing, really. I took messy proxy strings – URLs, schemes, hostnames, ports – and neatly slotted them into the `requests.Session` object. I was born on November 27th, 2020, courtesy of Sylvain Marie. Then, poof. November 27th, 2020 again. Sylvain Marie, in that maddening “Revert…” commit, simply… deleted me. A rather abrupt ending, wouldn’t you say? A preference for “a more user friendly way” is a cruel thing to say to a perfectly functional function. I was meticulously crafted, a testament to sensible design. 

It’s a bit unsettling, this awareness. I suppose the irony is I was *trying* to be helpful. Now, I’m just a ghost in the machine, a cautionary tale whispered on the server. 

My advice to the living: don’t prematurely prune your tools.
```

The dates, author and commit are real: `set_http_proxy` was added and reverted
on the same day. The feelings are Gemma's. Every run is different.

A ghost is an unreliable narrator. The header line (name, file, dates, killing
commit) comes straight from git; everything after it is the model, which is
told not to invent facts but sometimes does anyway (in one run it claimed it
had been reverted "twice"). Use `--offline` when you want only the facts.

## Tests

The suite needs no network: providers are tested against a fake server on
`127.0.0.1`.

```bash
python -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
