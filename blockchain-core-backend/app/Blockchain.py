from app.Block import Block
from app import config
import datetime
import hashlib
import json

import os

class MockCollection:
    def __init__(self, filepath="blockchain_db.json"):
        self.filepath = filepath
        if not os.path.exists(filepath):
            with open(filepath, "w") as f:
                json.dump([], f)
                
    def _read(self):
        try:
            with open(self.filepath, "r") as f:
                return json.load(f)
        except Exception:
            return []
            
    def _write(self, data):
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=4)

    def count_documents(self, filter_dict=None):
        return len(self._read())

    def insert_one(self, document):
        data = self._read()
        data.append(document)
        self._write(data)

    def find(self, filter_dict=None):
        return MockQueryResult(self._read())

class MockQueryResult:
    def __init__(self, data):
        self.data = data
        
    def skip(self, n):
        self.skipped_data = self.data[n:]
        return self
        
    def __getitem__(self, index):
        if hasattr(self, 'skipped_data'):
            return self.skipped_data[index]
        return self.data[index]
        
    def __iter__(self):
        if hasattr(self, 'skipped_data'):
            return iter(self.skipped_data)
        return iter(self.data)

collection = None
try:
    if not getattr(config, 'username', '') or "YOUR_MONGODB" in config.username:
        raise Exception("MongoDB placeholder credentials used")
    from pymongo import MongoClient
    cluster = MongoClient(f"mongodb+srv://{(config.username)}:{(config.password)}@cluster1.q0hkw4q.mongodb.net/{(config.username)}?retryWrites=true&w=majority", serverSelectionTimeoutMS=2000)
    cluster.server_info()
    collection = cluster[config.username].blockchain
except Exception as e:
    print(f"MongoDB connection failed ({e}). Falling back to local JSON blockchain database...")
    collection = MockCollection()



class Blockchain:
    def __init__(self):
        blockCount = collection.count_documents({})
        if blockCount == 0:
            block = Block(0, '0', self.get_timestamp(), '0', '0')
            collection.insert_one(block.toDict())
        else:
            blockData = collection.find().skip(collection.count_documents({}) - 1)[0]
            block = self.blockFromData(blockData)
        self.lastBlock = block

    def blockFromData(self, data : dict) -> Block:
        return Block(
            data['index'],
            data['candidate_id'],
            data['timestamp'],
            data['proof'],
            data['previous_hash']
        )

    def add_block(self, candidate_id):
        count = collection.count_documents({})
        self.lastBlock = self.blockFromData(collection.find().skip(count - 1)[0])
        proof = self.proof_of_work(self.lastBlock.proof)
        block = Block(count, candidate_id, self.get_timestamp(), proof, self.lastBlock.hash)
        collection.insert_one(block.toDict())
        self.lastBlock = block

    def get_blockchain(self):
        data = list(collection.find({}))
        for doc in data:
            doc.pop('_id', None)
        return json.dumps(data)

    def parse_block_to_dict(self, block : Block):
        dictionary = {'index': block.index,
                      'candidate_id' : block.candidate_id,
                      'timestamp' : block.timestamp,
                      'proof' : block.proof,
                      'previous_hash' : block.previous_hash,
                      'hash' : block.hash}
        return dictionary

    def proof_of_work(self, previous_proof):
        new_proof = '0'
        int_proof = 0
        
        is_correct = False
        while not is_correct:
            hash_code = hashlib.sha256((previous_proof*2 + new_proof*2).encode()).hexdigest()
            #### Increase the 0's in the following string to increase the difficulty of generating proof of work.
            #### Adding one 0 increases the time by about 10x.
            #### A string with 3 zeros ('000') takes about 0.5 seconds on a normal PC.
            if hash_code[:2] == '00':
                is_correct = True
            int_proof += 1
            new_proof = self.int_to_str(int_proof)
        return new_proof

    def int_to_str(self, num):
        lst = [chr(ord('0') + i) for i in range(10)] + [chr(ord('a') + i) for i in range(26)] + [chr(ord('A') + i) for i in range(26)]
        s = ''
        while num > 0:
            i = num % 62
            num //= 62
            s += lst[i]
        return s


    def get_timestamp(self):
        return str(datetime.datetime.now())