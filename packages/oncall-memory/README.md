# oncall-memory

P1. The long-term half of memory: the **playbook** of lessons, loaded from the committed
postmortems in `data/postmortems/`.

`docs/memory-policy.md` governs what may be stored and when. This package implements the
read side of it:

```python
from oncall_memory import LessonStore

store = LessonStore.from_postmortems()  # data/postmortems/*.md
store.playbook_hash  # pins the playbook in a RunRecord
store.search(service="payment", symptom="checkout error rate", status="approved")
```

`search` satisfies the interface `oncall_graph.handoffs.load_tools` already expects — an
object with `search(service, symptom, status)` — so the graph never imports this package.
The service constructs the store and injects it.

**This package does not write.** Proposing a lesson, approving one and `forget(incident_id)`
are later work; see `docs/demo-objectives-gap-analysis.md`.

## The one thing to understand before changing `search`

It filters on **status only**, never on preconditions, even though it is handed the service and
the symptom. Narrowing here would be an optimisation that deletes a demo objective: scene 3 shows
*an old lesson being rejected because its preconditions do not match*, and what it renders is the
`dropped` list that `partition_lessons` returns. A store that never hands over the stale lesson
leaves nothing to reject, and the scene silently becomes an empty list.
