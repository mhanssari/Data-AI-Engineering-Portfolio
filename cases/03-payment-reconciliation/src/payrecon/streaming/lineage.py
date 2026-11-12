"""Bind a persistent checkpoint marker to dataset, broker/topic identity and a database generation."""

import json
import uuid
from pathlib import Path

from payrecon.streaming.contract import Dataset, stamp
from payrecon.streaming.intake import Identity, bind_dataset, require_stream_database


def register(con, dataset: Dataset, identity: Identity, checkpoint: Path) -> str:
    require_stream_database(con)
    checkpoint = checkpoint.resolve()
    marker = checkpoint.with_name(checkpoint.name + ".lineage.json")
    expected = dict(
        dataset_id=dataset.dataset_id,
        source_start=stamp(dataset.source_start),
        source_end=stamp(dataset.source_end),
        cluster_id=identity.cluster_id,
        topic_id=identity.topic_id,
        topic=identity.topic,
        checkpoint_path=str(checkpoint),
    )
    with con.transaction():
        bind_dataset(con, dataset)
        if marker.exists():
            saved = json.loads(marker.read_text())
            if {key: saved.get(key) for key in expected} != expected:
                raise ValueError("checkpoint marker does not match dataset, clock, topic or cluster")
            generation = saved["generation"]
            registered = con.execute(
                "select dataset_id,cluster_id,topic_id,topic,checkpoint_path "
                "from stream.lineage where generation=%s",
                [generation],
            ).fetchone()
            if registered != (
                dataset.dataset_id,
                identity.cluster_id,
                identity.topic_id,
                identity.topic,
                str(checkpoint),
            ):
                raise ValueError("checkpoint marker has no matching database lineage")
            committed = con.execute(
                "select 1 from ops.stream_batch where generation=%s limit 1", [generation]
            ).fetchone()
            if committed and not (checkpoint / "metadata").is_file():
                raise ValueError(
                    "Spark checkpoint metadata is missing after a database commit; use a new lineage"
                )
            return generation
        if checkpoint.exists() and any(checkpoint.iterdir()):
            raise ValueError("unregistered checkpoint directory is not empty")
        if con.execute("select 1 from stream.lineage where checkpoint_path=%s", [str(checkpoint)]).fetchone():
            raise ValueError("checkpoint marker was removed from a registered path; do not silently reuse it")
        generation = str(uuid.uuid4())
        con.execute(
            "insert into stream.lineage(generation,dataset_id,cluster_id,topic_id,topic,checkpoint_path) "
            "values (%s,%s,%s,%s,%s,%s)",
            [
                generation,
                dataset.dataset_id,
                identity.cluster_id,
                identity.topic_id,
                identity.topic,
                str(checkpoint),
            ],
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("x") as handle:
            json.dump(dict(expected, generation=generation), handle, indent=2)
    return generation
