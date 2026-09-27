from config import Config
from host import Host

config = Config()

h1 = Host("H1", config)
h2 = Host("H2", config)

print(h1.id)
print(h1.address)
print(h1.neighbors)

print(h2.id)
print(h2.address)
print(h2.neighbors)

Host("A", config)