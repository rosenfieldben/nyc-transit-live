# Workflow scripts

Scripts in this directory are invoked as `Workflow({ name: "<file stem>" })`.

## The rule every review and probe workflow follows

**Every agent that could write runs with `isolation: "worktree"`.**

A review that verifies a finding the way this repository does, by applying the mutation
and seeing whether a spec dies, writes to the tree. Run over the shared checkout, those
writes race whatever the caller is doing.

This is not hypothetical. On stage MR2 of the map redesign, two verifier mutations landed
in `frontend/systems/subway.js` and `frontend/map.js` while the caller was running that
round's gates over the same files. One of them reached a commit, and that commit's gate
numbers therefore described a tree that was never the one committed. The agent had already
reverted its own edit by the time anyone noticed, which is what makes this class of bug
invisible: the evidence deletes itself. `docs/reviews/map-redesign-rounds.md` records it
under stage MR2's round 2.

Isolation costs a few hundred milliseconds and a little disk per agent. The alternative is
a review that can corrupt the thing it is reviewing.

It applies to **probe** workflows too, not only review ones: any script whose agents run
the app, write a fixture, take a screenshot into the repo, or apply a mutation to see what
dies. A read-only fan-out that only greps and reads does not need it, but a script cannot
promise its agents will stay read-only, so the default is isolation and the exception has
to be argued in the script.

## And the worktree must be at the commit under review

**A worktree is created at the repository default branch. Put it at the commit under review
and make every agent prove it.**

The isolation rule above says where an agent may write. It says nothing about what an agent
reads, and the default answer is the wrong thing: a fresh worktree lands on the default
branch, where the branch under review does not exist.

This is not hypothetical either. On stage MR3 of the map redesign, two verifiers read
`origin/main` and returned two real critical defects as REFUTED, with "the diff is empty"
and "`bindRailStationLabel` does not exist" as their evidence. One of the defects was a
canvas casing stroked with the literal string `var(--paper)`, which Canvas2D ignores
silently, leaving the previous branch's colour on the context; the other was a train
bearing computed from the wrong pair of points. Both were re-verified by hand and both were
real. `docs/reviews/map-redesign-rounds.md` records it under stage MR3's round 4.

A refutation that reads "the code you describe is not there" is indistinguishable from a
correct refutation of a hallucinated finding, which is what makes this the worst output a
review tool can produce: it deletes a real defect and looks like diligence doing it.

So a review workflow takes the sha of the tip under review, tells every agent to
`git checkout --detach <sha>` if it is not already there (detached, because N worktrees
share one object store and a branch checkout either fails or moves the caller's ref),
requires each agent to echo back the sha it actually read, and discards the output of any
agent whose sha does not match. A discarded verdict makes its finding **unverified**, never
refuted. `adversarial-review.js` implements this as its RULE 0b, and callers pass
`{commit: "<full sha>", branch: "<branch>"}` in `args`.

## And the caller owes one check back

Isolation makes the race impossible for agents a script spawns. It cannot make it
impossible for anything else that writes while you are not looking. So: **before any
commit, confirm the working tree is what the gates ran on**: `git status --short`, and a
diff against the last commit you controlled. A commit whose message cites gate numbers is
making a claim about a specific tree, and that claim is only true if the tree did not move
underneath it.
