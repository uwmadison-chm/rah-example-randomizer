# rah-example-randomizer
An example handler for redcap-alert-handler

This assigns a random value to a field in a REDCap project. It's not really intended to be useful; it's mainly a sample that will let me test the workings of this system, and let other folk see how this works. If you are just *randomizing* records, you should probably use REDCap's Randomization module.

## Configuration

Point a route at `rah-example-randomizer:randomize`:

```toml
[routes.randomizer]
handler = "rah-example-randomizer:randomize"
redcap_secrets_file = "/etc/rah/example-project-secrets.toml"
record_id_field = "study_id"
randomize_field = "arm"
random_values = ["control", "treatment"]
```

The handler reads the following rah configuration values:

* `redcap_secrets_file` (string) The path to a TOML file containing `redcap_api_url` and `redcap_api_token`
* `record_id_field` (string) The field that contains the project's record identifier
* `redcap_event_name` (string, optional) The event where we want to do randomization
* `randomize_field` (string) the name of the field you're going to set
* `random_values` (string array) the set of values to randomize from

## What it does

This will randomize one record at a time -- each email should be one randomization trigger. The email will contain the record id, in TOML format. We expect the message to contain just `record_id` (string) -- the name of the record we're going to randomize. (Other uses may contain many more keys.) Other keys will be ignored.

Once we read record_id, we will:

* Read the redcap_secrets_file to get the API address and token
* Pick a random value from `random_values` and record it, along with record_id, in a sqlite database in the route's state directory. If the record is already in the database, we keep the value that's already stored; if it's already marked complete, we return immediately.
* Connect to the REDCap API and import the stored value
* If that succeeds, mark the record complete in the database and return
* If the connection to the REDCap server times out, raise TransientError
* If the import fails for any other reason, or the secrets don't get us in to REDCap, raise PermanentError
* For any other unexpected exception, raise PermanentError

The value gets written to the database *before* the import, not after: rah abandons a handler that times out rather than killing it, so the first attempt can still be running while a retry starts. Recording the value first means every attempt for a record imports the same value, instead of each attempt rolling its own.

## Running the tests

From this directory, `uv run pytest`. From the rah workspace root, `uv sync --all-packages` and then `uv run pytest rah-example-randomizer/tests`.
