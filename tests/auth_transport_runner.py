"""Queue-only UDS runner: fake authority by default, actual runtime with --workspace."""
import argparse
from functools import partial
from pathlib import Path

from health_buddy.production_server import serve
from tests.auth_transport_fixtures import fake_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.workspace is not None:
        from health_buddy.config import load
        from health_buddy.security_runtime import open_runtime
        ingress = load(args.workspace).ingress()
        assert ingress.socket_path == args.socket
        factory = partial(open_runtime, args.workspace)
    else:
        factory = partial(fake_runtime, args.socket, uds=True)
        ingress = factory().ingress
    serve(factory, ingress=ingress)


if __name__ == "__main__":
    main()
