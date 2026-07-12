# rah-example-randomizer
An example handler for redcap-alert-handler

This will output a CSV file that assigns a random value to a field in a REDCap project. It's not really intended to be useful; it's mainly a sample that will let me test the workings of this system, and let other folk see how this works. If you are just *randomizing* records, you should probably use REDCap's Randomization module.

The following rah configuration values:

* `redcap_secrets_file` (string) The path to a TOML file containing `redcap_api_url` and `redcap_api_token`
* `record_id_field` (string) The field that contains the project's record identifier
* `redcap_event_name` (string, optional) The event where we want to do randomization
* `randomize_field` (string) the name of the field you're going to set
* `random_values` (string array) the set of values to randomize from

This will randomize one record at a time -- each email should be one randomization trigger. The email will the record id, in TOML format. We expect the message to contain just `record_id` (string) -- the name of the record we're going to randomize. (Other uses may contain many more keys.)

Other keys will be ignored.

Once we read record_id, we will:

* Read the redcap_secrets_file to get API address and key
* Get record_id from the email
* In a data store (likley a sqlite database set to properly handle contention), we look up record_id; if it's there, return immediately
* Get a random value from `randomize_values`
* Connect to the REDCap API and import the random value.
* If that succeeds, log the record_id and randomized value in the data store and return
* If the import fails for any reason  or the import fails or the secrets don't get us in to REDCap, raise PermanentError
* If the connection to the REDCap server times out, raise TransientError
* For any other unexpected exception, raise PermanentError
