"""Own the local writer guard for exactly as long as its backend is open."""

from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.storage import LocalStorage
from visp_memory.core.writer_lock import WriterLockHandle, acquire_writer_lock


def open_local_storage(config, embedding_fn=None):
    if config.storage.backend == "neo4j":
        return Neo4jStorage(
            uri=config.storage.neo4j_uri,
            user=config.storage.neo4j_user,
            password=config.storage.neo4j_password,
            embedding_fn=embedding_fn,
            turn_keys=config.embedding.turn_keys,
        ), WriterLockHandle()

    handle = acquire_writer_lock(config.storage.data_dir, "local")
    try:
        if config.storage.backend == "arcadedb":
            storage = ArcadeDbStorage(config.storage.data_dir, embedding_fn=embedding_fn)
        else:
            storage = LocalStorage(
                config.storage.data_dir, embedding_fn=embedding_fn,
                turn_keys=config.embedding.turn_keys,
            )
        return storage, handle
    except BaseException:
        handle.release()
        raise
