# TYPING_BRIEF — retired

> **This work is DONE (3.0) and this brief is retired.** It was the hand-off
> for clearing the `D`/`ANN` exemptions on the two legacy scripts.
>
> **The full account is in `CODE_REVIEW.md` §20.** The short version: both
> scripts went to 100% annotated (engine 89/89 returns and 200/200 arguments;
> GUI 118/118 and 84/84), every exemption and `ignore_errors` override was
> deleted, and mypy now gates all 35 files. Turning the checker on found seven
> of my own annotations flatly contradicted by the code, a second
> `list`-invariance defect, and a latent `None` dereference in the tes3cmd
> worker.
>
> The method it described — annotate from call sites, then enable mypy
> **per module** before moving on — is worth reusing and is recorded in §20.

This file is kept only so existing references resolve. It can be deleted.
