# Opt out

Anyone listed in the global file, including a founding entry, can ask to be removed.

Open an issue with the [opt-out template](../.github/ISSUE_TEMPLATE/opt-out.md). Include the numeric user id if you have it, or the handle. The maintainer adds that user id to [`accounts.json`](accounts.json). The next rebuild drops the account. A removal wins over a recommendation and over the founding waiver.

Leaving community mode on a local bot does not remove a list that was already merged. The owner files this opt-out, or asks the maintainer to, when they want the public row gone.

Banned owners live in [`banned/owners.json`](../banned/owners.json). Their later submissions are ignored. Banning an owner does not delete the founding seed.
