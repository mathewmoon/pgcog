from string import ascii_letters as letters
import json
from re import match
from time import sleep
import random
from datetime import datetime
import boto3
import psycopg2
from psycopg2.extras import DictCursor
from botocore.exceptions import ClientError
from auroraPrettyParser import parseResults
from .cognition_functions import validateConfig, getCallerAccount

class DatabaseClient():
    """
    :Keyword Arguments:
        * *client_type* -- (``str``) -- "serverless" for Aurora Serverless, otherwise "instance". Defaults to "instance"
        * *event* (``list or dict``) -- event argument from Lambda function
        * *config* (``dict``) -- configuration options
            * *region* -- (``str``, optional) aws region. Defaults to us-east-1
            * *database* -- (``str``) database name
            * *databaseArn* -- (``str``) arn of the database
            * *databaseSecretArn* -- (optional) Arn of the database secret to use. Defaults to caller's secret
            * *account* -- (optional) Account number where secrets live. Defaults to account number resolved by cognition_functions.getCallerAccount()
            * *secretsPath* -- (optional) Path to AWS secrets. Defaults to rds-db-credentials/
            * *roleOverrides* -- (``dict``, optional) a dictionary of {<role arn>: <secret name>} to match for auth
            * *assumedRoleOverrides* -- (``dict``, optional) a dictionary of {<role name>: <secret name>} to match for auth. Used to auth assumed roles (cli users for instance)
    :returns: An instance of PgCognition.DatabaseClient
    :rtype: PgCognition.DatabaseClient

    :Environment: Options required in config can be omitted if they exist in os.environ. Options to be taken from the environment should have their names specified in all caps.

    *Example with a standard Postgres database*

    ..  code-block:: python

        from PgCognition import DatabaseClient

        config = {
            "dbname": "mydb",
            "user": "myuser",
            "host": "localhost",
            "password": "password"
        }
        c = DatabaseClient(config=config, client_type="instance")
        res = c.runQuery("SELECT * FROM mytable", switch_role="less_privileged_role")

    *Example with a Aurora Serverless*

    ..  code-block:: python

        from PgCognition import DatabaseClient

        config = {
            "database": "mydb",
            "account": 12345678,
            "databaseArn": "arn:aws:rds:us-east-1:123456789:db:mydb",
            "assumedRoleOverrides": {"administrator": "root-secret", "developer": "root-secret"},
            "region": "us-east-1",
            "secretsPath": "rds-db-credentials"
        }
        dbClient = DatabaseClient(event=event, config=config)
        return dbClient.resolveAppsyncQuery()
        res = c.runQuery("SELECT * FROM mytable", switch_role="less_privileged_role")
    """

    def __init__(self, event=None, config={}, client_type="instance"):
        assert client_type in ("serverless", "instance"), "kwarg client_type must be one of 'serverless' or 'instance'"
        self.event = event

        if client_type == "serverless":
            defaults = {"account": getCallerAccount(), "region": "us-east-1", "secretsPath": "rds-db-credentials"}
            required = ("dbname", "databaseArn", "account", "region")
            self.config = validateConfig(required, config, defaults=defaults)
            self.client = boto3.client("rds-data")
            self.client_type = "serverless"
        else:
            required = ("dbname", "host", "user", "password", "port")
            defaults = {"port": 5432}
            self.config = validateConfig(required, config, defaults=defaults)
            self.client = psycopg2.connect(
                dbname=self.config["dbname"],
                user=self.config["user"],
                host=self.config["host"],
                password=self.config["password"],
                port=self.config["port"]
            )
            self.client_type = "instance"
            self.commit = self.client.commit

    def close(self):
        """Close the database connection for client_type of "instance"

        :Note: This will throw an AttributeError if client_type is not "instance"

        :returns: None
        :rtype: None
        """

        if self.client_type == "instance":
            self.client.close()

    def runQuery(self, sql, **kwargs):
        """Run an SQL query HELLO

        This method is a convenience wrapper for runInstanceQuery and runServerlessQuery
        The method called whose return value is returned from this method and whose arguments
        should be passed will depend on self.client_type

        See PgCognition.DatabaseClient.runInstanceQuery and PgCognition.DatabaseClient.runServerlessQuery for arguments

        :returns: List or Dictionary of query results
        :rtype: list or dict

        *Example with a standard Postgres database*

        ..  code-block:: python

            from PgCognition import DatabaseClient

            config = {
                "dbname": "mydb",
                "user": "myuser",
                "host": "localhost",
                "password": "password"
            }
            c = DatabaseClient(config=config, client_type="instance")
            res = c.runQuery("SELECT * FROM mytable", switch_role="less_privileged_role")
        """

        if isinstance(self.client, psycopg2.extensions.connection):
            res = self.runInstanceQuery(sql, **kwargs)
        else:
            res = self.runServerlessQuery(sql, **kwargs)
        return res

    def runInstanceQuery(self, sql, **kwargs):
        r"""Run a query against an Postgresql database

        :param sql: SQL statement to execute
        :type sql: str, required

        :Keyword Arguments:
            * *pretty* (``bool``) -- format the results as a list of dicts, one per row, with the keys as column names, default True
            * *parameters* (``list``) -- a list of parameters to pass to the query in psycopg2's format
            * *switch_role* (``string``) -- Run "SET ROLE <switch_role>" before executing the query to escalate/deescalate privileges
            * *commit* (``bool``) -- Commit directly after query
        """
        commit = False if "commit" not in kwargs else kwargs["commit"]
        assert commit in (True, False), "commit kwarg must be a boolean"

        if "switch_role" in kwargs and kwargs["switch_role"] is not None:
            sql = f"""SET ROLE {kwargs["switch_role"]}; {sql}"""
        pretty = True if "pretty" not in kwargs else kwargs["pretty"]
        parameters = {} if "parameters" not in kwargs else kwargs["parameters"]
        cursor_type = DictCursor if pretty else None
        c = self.client.cursor(cursor_factory=cursor_type)
        c.execute(
            sql,
            parameters
        )
        if commit: self.client.commit()
        if pretty: r = [dict(x) for x in c.fetchall()]
        else: r = [list(x) for x in c.fetchall()]
        return r

    def runServerlessQuery(self, sql, **kwargs):
        r"""Run a query against an Aurora Serverless Postgresql database

        :param sql: SQL statement to execute
        :type sql: str, required

        :Keyword Arguments:
            * *schema* (``str``) -- schema to use. Boto3 does not currently respect this. Use the full path to your table in the query instead
            * *pretty* (``bool``) -- format the results as a list of dicts, one per row, with the keys as column names, default True
            * *parameters* (``list``) -- a list of parameters to pass to the query
            * *secret* (``str``) -- override config["databaseSecretArn"]
            * *database* (``str``) -- override config["database"]
            * *databaseArn* (``str``) -- override config["databaseArn"]

        :returns: AWS Aurora rds-data response, optionally formatted with auroraPrettyParser
        :rtype: list or dict
        """
        schema = "" if "schema" not in kwargs or kwargs["schema"] is None else kwargs["schema"]
        pretty = True if "pretty" not in kwargs else kwargs["pretty"]
        parameters = [] if "parameters" not in kwargs else kwargs["parameters"]
        database = self.config["database"] if "database" not in kwargs else kwargs["database"]
        databaseArn = self.config["databaseArn"] if "databaseArn" not in kwargs else kwargs["databaseArn"]
        if "secret" in kwargs:
            secret = kwargs["secret"]
        elif "databaseSecretArn" in self.config:
            secret = self.config["databaseSecretArn"]
        else:
            secret = self.getCredentials()

        res = self.client.execute_statement(
            secretArn=secret,
            schema=schema,
            database=database,
            resourceArn=databaseArn,
            parameters=parameters,
            includeResultMetadata=pretty,
            sql=sql
        )
        if pretty: res = parseResults(res)
        return res

    def resolveAppsyncQuery(self, schema="", secretArn=None, client_type=None, switch_role=None):
        """Run a query for each item in event

        This is a wrapper for DatabaseClient.resolveInstanceAppsyncQuery and DatabaseClient.resolveServerlessAppsyncQuery and
        will return the result of one of these two methods, depending on whether client_type is "serverless" or "instance".
        See PgCognition.DatabaseClient.resolveInstanceAppsyncQuery and PgCognition.DatabaseClient.resolveServerlessAppsyncQuery

        :Keyword Arguments:
            * *schema* (``str``) -- Schema name. Boto3 rds-client doesn't acually honor this right not. It is best to use the full path to your table in your query. Only used if client_type = "serverless"
            * *secretArn (``str``) -- Override databaseSecretArn from config. Only used if client_type = "serverless"
            * *switch_role* (``str``) -- Excute query as specific user. The current connection must use a user with permission to assume this role. Only used if client_type = "instance"

        :returns: Query results from PgCognition.DatabaseClient.resolveInstanceAppsyncQuery or PgCognition.DatabaseClient.resolveServerlessAppsyncQuery
        :rtype: dict or list
        """

        if client_type is None:
            client_type = self.client_type
        if client_type == "serverless":
            res = self.resolveServerlessAppsyncQuery(schema=schema, secretArn=secretArn)
        else:
            res = self.resolveInstanceAppsyncQuery(switch_role=switch_role)
        return res

    def resolveInstanceAppsyncQuery(self, switch_role=None):
        """Run a query for each item in the event.

        :Keyword Arguments:
            * *switch_role* (``str``) -- Excute query as specific user. The current connection must use a user with permission to assume this role

        :returns: list of dicts or list of list of dicts with column names as keys
        :rtype: list
        """

        if isinstance(self.event, list):
            res = []
            for n in range(len(self.event)):
                result = self.runInstanceQuery(
                    self.event[n]["query"],
                    parameters=self.event[n]["parameters"],
                    switch_role=switch_role
                )
                res.append(result)

        else:
            result = self.runInstanceQuery(
                self.event[n]["query"],
                parameters=self.event["parameters"],
                switch_role=switch_role
            )
        return result

    def resolveServerlessAppsyncQuery(self, schema="", secretArn=None):
        """Run a query for each item in the event.

        :Keyword Arguments:
            * *schema* (``str``) -- Schema name. Boto3 rds-client doesn't acually honor this right not. It is best to use the full path to your table in your query.
            * *secretArn (``str``) -- Override databaseSecretArn from config

        :returns: list of dicts or list of list of dicts with column names as keys
        :rtype: list
        """
        try:
            if secretArn is not None:
                secret = secretArn
            elif "databaseSecretArn" in self.config and secretArn is None:
                secret = self.config["databaseSecretArn"]
            else:
                secret = self.getCredentials()

            if isinstance(self.event, list):
                res = []
                for n in range(len(self.event)):
                    result = self.runServerlessQuery(
                        self.event[n]["query"],
                        schema=schema,
                        parameters=self.event[n]["parameters"],
                        secret=secret
                    )
                    res.append(result)
            else:
                res = self.runServerlessQuery(
                    self.event["query"],
                    schema=schema,
                    parameters=self.event["parameters"],
                    secret=secret
                )
        except ClientError as e:
            res = e

        return res

    def getCredentials(self):
        """
        Get ARN for the secret containing the RDS credentials for the user who called us.

        Secrets for cognito users are constructed as:
            arn:aws:secretsmanager:{region}:{account}:{secretsPath}/{event["identity"]["email"]}

        We will return the first credential found in the order of:
            1. If caller is a Cognito user
            2. If caller is matched in roleOverrides
            3. If caller is matched in assumedRoleOverrides

        :returns: The arn of an AWS secret
        :rtype: str
        """

        if isinstance(self.event, list):
            identity = self.event[0]["identity"]
        else:
            identity = self.event["identity"]

        secret = None
        secretBase = f"""arn:aws:secretsmanager:{self.config["region"]}:{self.config["account"]}:secret:{self.config["secretsPath"]}"""
        if "userArn" not in identity:
            secret = f"""{secretBase}/{identity["claims"]["email"]}"""
        elif self.config["roleOverrides"]:
            for arn in self.config["roleOverrides"]:
                if identity['userArn'] == arn:
                    secret = f"""{secretBase}/{self.config["roleOverrides"][arn]}"""
                if secret is not None: break
        elif self.config["assumedRoleOverrides"]:
            for roleName in self.config["assumedRoleOverrides"]:
                if match(f'^arn:aws:sts::[0-9]+:assumed-role/{roleName}/[0-9]+$', identity["userArn"]):
                    secret = f"""{secretBase}/{self.config["assumedRoleOverrides"][roleName]}"""
                if secret is not None: break
        if secret is None:
            raise Exception("Could not determine secret ARN through cognito claims or IAM overrides")
        return secret

    def createDatabaseUser(self, login=False, email=None):
        """Creates a database user, optionally from the email address found in the Lambda event

        :Keyword Arguments:
            * *login* (``bool``) -- Whether or not this will be a login user
            * *email* (``str``) -- Override the email address from self.event["user"]["email"]

        :returns: Dictionary containing new user's database credentials and corresponding user from cognition.users table
        :rtype: dict


        :Note: If login=True then a random password will be generated and the user granted LOGIN privileges, if False then this will
        be a role that can only be used to switch to after login. EG: using switch_role option to PgCognition.DatabaseClient.runInstanceQuery()
        """

        dbpass = ''.join(random.choice('!@#$%^&*()_-+=1234567890' + letters) for i in range(31))
        email = email if email is not None else self.event["user"]["email"]
        try:
            if self.client_type == "serverless":
                parameters = [
                    {'name': 'EMAIL', 'value': {'stringValue': f'{email}'}},
                    {'name': 'CURRENT_TIME', 'value': {'stringValue': f'"{datetime.now().isoformat()}"'}}
                ]

                sql = """
                    UPDATE pg_cognition.users
                    SET
                        status='active',
                        invitation_data=jsonb_set(invitation_data, '{accepted_date}', (:CURRENT_TIME)::JSONB)
                    WHERE email = :EMAIL
                    RETURNING *;
                """
                newuser = self.runQuery(sql, parameters=parameters)

                if not newuser:
                    raise Exception(f"""Application user {email} does not exist.""")

                newuser = newuser[0]
                loginSql = f"""
                    CREATE USER {newuser["id"]} WITH PASSWORD '{dbpass}' IN ROLE {newuser["invitation_data"]["role"]};
                """

                nologinSql = f"""
                    CREATE ROLE {newuser["id"]} IN ROLE {newuser["invitation_data"]["role"]};
                """
                sql = loginSql if login else nologinSql
                self.runQuery(sql)
                newuser["secret"] = self.createSecret(newuser["id"], email, dbpass) if login else None
            else:
                parameters = {
                    "CURRENT_TIME": datetime.now().isoformat(),
                    "EMAIL": email
                }
                sql = f"""
                    UPDATE pg_cognition.users
                    SET
                        status='active'
                        invitation_data=jsonb_set(invitation_data, '{{accepted_date}}', (%(CURRENT_TIME)s)::JSONB)
                    WHERE email = %(EMAIL)s RETURNING *;
                """
                newuser = self.runQuery(sql, parameters=parameters)

                if not newuser:
                    raise Exception(f"""Application user {email} does not exist.""")

                newuser = newuser[0]
                loginSql = f"""
                    CREATE USER {newuser["id"]} WITH PASSWORD '{dbpass}' IN ROLE {newuser["invitation_data"]["role"]};
                """

                nologinSql = f"""
                    CREATE ROLE {newuser["id"]} IN ROLE {newuser["invitation_data"]["role"]};
                """
                sql = loginSql if login else nologinSql
                self.runQuery(sql)
                newuser["password"] = dbpass if login else None

            return newuser
        except (Exception, ClientError) as e:
            try:
                sql = f"""
                    UPDATE pg_cognition.users SET status = 'invited' WHERE id = {newuser["id"]};
                    REVOKE {newuser["invitation_data"]["role"]} FROM {newuser["id"]};
                    DROP ROLE {newuser["id"]}
                """
                self.runQuery(sql)
            except Exception:
                pass
            if self.client_type == "serverless" and login:
                try:
                    self.deleteSecret(newuser["email"], wait=False)
                except Exception:
                    pass
            raise e

    def deleteSecret(self, user, wait=True):
        """Deletes an AWS Secret
        :param user:  Email address of the user to remove the secret for
        :type user: str

        :Keyword Arguments:
            * *wait* (``bool``) -- Block until delete is complete
        """
        s = boto3.client("secretsmanager")
        s.delete_secret(
            SecretId=f'{self.config["secretsPath"]}/{user}',
            ForceDeleteWithoutRecovery=True
        )
        if wait:
            while s.describe_secret(SecretId=f'{self.config["secretsPath"]}/{user}'):
                sleep(5)

    def createSecret(self, userid, email, dbpass, upsert=True):
        """Creates an AWS Secret to use for database credentials

        :param userid: The database username to put in the secret
        :type userid: str
        :param email: The cognito email address for the database user
        :type email: str
        :param dbpass: Database password to store
        :type dbpass: str

        :Keyword Arguments:
            * *upsert* (``bool``) -- If True (default) then update the secret if a secret for this email address. Other wise an Exception will be raised.

        """
        s = boto3.client('secretsmanager')
        secretString = {
            "dbClusterIdentifier": self.config["databaseArn"],
            "engine": "postgres",
            "host": self.config["databaseHost"],
            "password": dbpass,
            "port": 5432,
            "username": userid
        }

        if s.describe_secret(SecretId=f'{self.config["secretsPath"]}/{email}'):
            if not upsert:
                raise Exception(f"""Secret {self.config["secretsPath"]}/{email} already exists. Use upsert=True to update or delete first""")
            arn = s.update_secret(
                SecretId=f'{self.config["secretsPath"]}/{email}',
                Description=f'DB credentials for {email}',
                SecretString=json.dumps(secretString)
            )["ARN"]
        else:
            arn = s.create_secret(
                Name=f'{self.config["secretsPath"]}/{email}',
                Description=f'DB credentials for {email}',
                SecretString=json.dumps(secretString)
            )["ARN"]

        return arn
