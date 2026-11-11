# dbt project location

Status: not initialised. No dbt commands are valid yet.

At implementation time select supported dbt and Snowflake adapter versions and initialise the actual project here. Keep staging, intermediate and mart models under models/. Add macros, tests, small seeds and snapshots only where needed. Track sources and model documentation in the dbt project.

This scaffold deliberately has no dbt_project.yml, profiles.yml, package configuration or sample models. Keep real profiles and credentials out of Git; a profiles.example.yml may show dummy configuration. Generated target/, dbt_packages/ and logs/ are ignored.
