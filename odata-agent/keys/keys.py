# import json

# with open("ca/ca.crt", "r") as f:
#     ca_cert = f.read()

# with open("client/C013.crt", "r") as f:
#     client_cert = f.read()

# with open("client/C013.key", "r") as f:
#     client_key = f.read()

# config = {
#     "agent_id": "agent-001",
#     "auth_token": "replace-with-token",
#     "server_host": "datasyn.company.internal",
#     "server_port": 8443,
#     "trusted_ca_cert": ca_cert,
#     "client_cert": client_cert,
#     "client_private_key": client_key
# }

# with open("config.json", "w") as f:
#     json.dump(config, f, indent=2)

# print("config1.json created successfully.")


import json


with open("ca/ca.crt", "r") as f:
    ca_cert = f.read()


with open("server/client_svr_server.crt", "r") as f:
    server_cert = f.read()


with open("server/client_svr_server.key", "r") as f:
    server_key = f.read()


with open("datasyncer/client_svr_datsyncher.crt", "r") as f:
    datasyncer_cert = f.read()


with open("datasyncer/client_svr_datsyncher.key", "r") as f:
    datasyncer_key = f.read()


config = {
    "agent_id": "agent-001",
    "auth_token": "replace-with-token",

    "server": {
        "host": "datasyn.company.internal",
        "port": 8443,
        "certificate": server_cert,
        "private_key": server_key
    },

    "datasyncher": {
        "host": "datasyncher.company.internal",
        "port": 8444,
        "certificate": datasyncer_cert,
        "private_key": datasyncer_key
    },

    "trusted_ca_cert": ca_cert
}


with open("sr_dtsn_config.json", "w") as f:
    json.dump(config, f, indent=2)


print("sr_dtsn_config.json created successfully.")