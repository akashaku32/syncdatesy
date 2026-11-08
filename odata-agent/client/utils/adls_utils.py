
# this is the mapping used to create metadata file for writing in adls 
EDM_TYPE_MAPPING = {
    "Edm.Binary": "BINARY",
    "Edm.Boolean": "BOOLEAN",
    "Edm.Byte": "BINARY",
    "Edm.DateTime": "NVARCHAR",
    "Edm.Decimal": "DECIMAL",
    "Edm.Double": "DOUBLE",
    "Edm.Guid": "STRING",
    "Edm.Int16": "SHORT",
    "Edm.Int32": "INTEGER",
    "Edm.Int64": "LONG",
    "Edm.SByte": "BINARY",
    "Edm.Single": "FLOAT",
    "Edm.String": "NVARCHAR",
    "Edm.Time": "NVARCHAR",
    "Edm.DateTimeOffset": "INSTANT"
}


PROPERTIES ={
    "NVARCHAR":[
        {
          "namespace": "com.datasynchor.vflow",
          "name": "vtype-template",
          "value": "string"
        }
      ],
    "DECIMAL":[
        {
          "namespace": "com.datasynchor.vflow",
          "name": "vtype-template",
          "value": "decfloat34" 
        }
      ]

}

import random

def rand_hex(n: int) -> str:
    return ''.join(random.choice('0123456789abcdef') for _ in range(n))

def generate_filename_base():
    a = str(random.randint(1000000000, 9999999999))
    b = rand_hex(8)
    c = rand_hex(4)
    d = rand_hex(4)
    e = rand_hex(4)
    f = rand_hex(12)

    return f"part-{a}-{b}-{c}-{d}-{e}-{f}"


def generate_filename(base_name, part_no=1):
    nn = f"{part_no:02d}"
    return f"{base_name}-{nn}.parquet"