"""Mock Server 用の自己署名CAとワイルドカード証明書を生成する。

クローラーは robots.txt / sitemap を https 固定で取得するため、Mock Server は TLS で待ち受ける。
クローラー側では ca-bundle.pem（certifi の CA 一覧 + この CA）を信頼させる。

使い方（リポジトリのルートで実行）:
  python -m benchmark.mock.gen_cert --suffix bench.internal

出力（benchmark/mock/certs/）:
  ca.crt / ca.key          自己署名CA
  server.crt / server.key  *.<suffix>, <suffix>, localhost, 127.0.0.1 用のサーバー証明書
  ca-bundle.pem            certifi の CA 一覧に ca.crt を追加したもの（クライアント側で使う）
"""
from __future__ import annotations
import argparse
import datetime as dt
import ipaddress
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_DIR = Path(__file__).parent / "certs"
VALID_DAYS = 365


def _write_key(key, path: Path) -> None:
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate TLS certificates for the mock server")
    parser.add_argument("--suffix", default="bench.internal", help="domain_suffix of the scenarios")
    parser.add_argument("--out", default=str(CERT_DIR))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    not_after = now + dt.timedelta(days=VALID_DAYS)

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Benchmark Mock CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = ec.generate_private_key(ec.SECP256R1())
    san = x509.SubjectAlternativeName([
        x509.DNSName(f"*.{args.suffix}"),
        x509.DNSName(args.suffix),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"*.{args.suffix}")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )

    pem = serialization.Encoding.PEM
    (out / "ca.crt").write_bytes(ca_cert.public_bytes(pem))
    _write_key(ca_key, out / "ca.key")
    (out / "server.crt").write_bytes(server_cert.public_bytes(pem))
    _write_key(server_key, out / "server.key")
    bundle = Path(certifi.where()).read_bytes().rstrip(b"\n") + b"\n\n" + ca_cert.public_bytes(pem)
    (out / "ca-bundle.pem").write_bytes(bundle)
    print(f"written: {out} (valid until {not_after:%Y-%m-%d}, SAN *.{args.suffix})")


if __name__ == "__main__":
    main()
