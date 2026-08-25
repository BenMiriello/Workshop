# Reviewing the five plan sets, and what they changed

**Status: current, 2026-08-24.** Five Opus agents planned five areas and filed
37 tickets. This is the review pass over their output: what I verified, what I
settled, and the two things they overturned that I had told the operator.

## 1. Two of my own claims were wrong, both measured

**The cost distribution.** I reported a median run cost of about 800,000 tokens
and said admission under-reserves by five to seven times. Measured over all 168
`billed` lines rather than a hand-picked six:

    count 168   min 24,559   median 107,284   p80 231,965   max 1,045,598

The six figures I quoted are real and are the TOP of the distribution. The
nominal reserve of 150,000 is **above** the median. Retracted in
`limiting-usage-2026-08-24.md`.

The conclusion survives and is strengthened: the spread is **42.6x**, not 6x, so
the case that cost cannot be estimated is much stronger than I made it. That is
the argument for pause-and-resume and against tuning the reserve.

**The lease refactor was not a blocker.** `work_review.py`'s docstring says the
other half of C2 is blocked on "the lease refactor (B3)". Verified: B3 shipped
in commit `d4c82846` eight hours after that paragraph was written, and the
paragraph has never been edited. `db/lease_repo.py` (296 lines),
`services/leases.py` (543), `scripts/lease.py` and migration
`f2a6d0c31b78_a_lease_is_who_may_write_here` all exist. So the fix for the
duplicated-work defect was never blocked; it was waiting on a stale comment.

## 2. The defect that actually caused the ten branches

`tasks.reclaim`'s docstring says the monotonic `claim_token` is "left intact so a
zombie that later tries to finalize is fenced". Leaving it intact means the
zombie's token still MATCHES. The fence only closes when the NEXT worker claims,
so in the window between reclaim and re-claim a crashed worker's late write of
`done` is accepted. Demonstrated by running the real module.

**A comment asserting a property the code does not have is now the second
instance today**, alongside the stale B3 paragraph. Both cost real work: one hid
a fixable defect, the other deferred a fix nobody needed to defer. Worth naming
as a class, because a docstring is not a test and this repo keeps treating it as
one.

## 3. What I settled, so it does not sit as an open question

**The extraction order for the middle four apps.** Two agents disagreed. Both
independently reached health-first and birds-last. For budget, finance, commute
and location the loader agent ordered by CSS debt (a cost argument) and the
extraction agent ordered by dependency (`budget` needs `finance.read`, `commute`
needs `location.read`), so that a provider leaves only after its consumers.

**The dependency order wins.** Extracting a provider first degrades the
operator's live instance, which is a correctness cost paid immediately; CSS debt
is effort, paid once and recoverable. Order: health, notes-app, finance, budget,
commute, location, birds.

Correction to my own framing while settling it: the extraction agent wrote the
rule as "a provider leaves after its consumers", which puts the CONSUMER first.
Budget consumes finance, so budget moves before finance.

**One install mechanism, not two.** The extraction plan's acceptance for the
birds backend requires `import main` to succeed "with it installed from the
overlay", and the loader plan separately designs a platform-owned tarball channel
so an out-of-repo app can obtain the kit. Those are the same problem, once for
Python and once for JavaScript, and neither plan references the other. They must
be one mechanism with two adapters, or this repo grows its signature defect: the
same concept implemented twice, diverging.

## 4. What I checked and found sound

- 53 tickets referencing today's plans, **zero fail the readiness gate**.
- No shell mangling in any ticket body (one agent reported repairing a ticket
  whose text was eaten by backtick substitution; the repair holds).
- The artifact spec and the extraction plan do NOT contradict each other on the
  four in-process Python apps: both keep them mounted in-process and move only
  their files. I checked this specifically because it looked like a contradiction.
- The artifact spec's central call, that a UI bundle and a hosted service are
  two artifacts in one release rather than one artifact, is right and corrects
  the hosting design's tier 1: serving a shell plugin from the platform's own
  operator-gated origin is not hosting, and "costs nothing per app" was true but
  vacuous.

## 5. A store limitation every agent hit independently

Priority is stored as a **named band**, not an integer: 100 urgent, 80 high, 40
medium, 1 low. All five agents filed with fine-grained priorities and all five
found them collapsed. Sequence is carried by `depends_on` and nothing else.

This is worth knowing before anyone tunes a priority again: within a band, order
falls back to creation time. A plan that encodes its sequence in priority numbers
has not encoded it at all.

## 6. Still the operator's

Unchanged and not filed: whether Workshop is ever hosted for others (gates most
of the hosting work); the monthly limit's value and unit, where the recommendation
is 60,000,000 weighted tokens rather than dollars, because every writer on this
installation is a subscription CLI turn and a dollar limit would silently never
bind; whether `.muted` and `.card` are inlined or promoted; and what `done`
claims, which is reached by two paths with opposite trunk implications.
