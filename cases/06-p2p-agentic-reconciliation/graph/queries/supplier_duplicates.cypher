// Real-world suppliers held under more than one source identifier, with how many of them span both ERPs.
MATCH (source:Supplier)-[:RESOLVES_TO]->(golden:GoldenSupplier)
WITH golden, collect(source.sourceSystem + ':' + source.sourceKey) AS members,
     collect(DISTINCT source.sourceSystem) AS systems, collect(source.partyName) AS names
WHERE size(members) > 1
RETURN golden.goldenId AS supplier_gid, golden.partyName AS golden_name, members, names, size(systems) = 2 AS across_erps
ORDER BY supplier_gid
