"""Strict PROXY v1 decoding, only on an explicitly trusted load balancer path."""
import ipaddress
import socket


def source_ip(sock: socket.socket, direct_ip: str, networks: tuple) -> str:
    if not networks:
        return direct_ip
    address = ipaddress.ip_address(direct_ip)
    if not any(address in network for network in networks):
        raise ValueError("Il listener PROXY accetta solo bilanciatori attendibili.")
    sock.settimeout(3)
    header = bytearray()
    while len(header) < 108 and not header.endswith(b"\r\n"):
        byte = sock.recv(1)
        if not byte:
            raise ValueError("Header PROXY incompleto.")
        header.extend(byte)
    if not header.endswith(b"\r\n"):
        raise ValueError("Header PROXY troppo lungo.")
    fields = header[:-2].decode("ascii").split(" ")
    if len(fields) != 6 or fields[0] != "PROXY" or fields[1] not in ("TCP4", "TCP6"):
        raise ValueError("Header PROXY non valido.")
    source, destination = ipaddress.ip_address(fields[2]), ipaddress.ip_address(fields[3])
    version = 4 if fields[1] == "TCP4" else 6
    if source.version != version or destination.version != version:
        raise ValueError("Famiglia PROXY non valida.")
    for port in fields[4:]:
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Porta PROXY non valida.")
    return str(source)
