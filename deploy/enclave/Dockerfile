# syntax=docker/dockerfile:1.7
# This setup's Enclave image. Built HERE from the signed EKKA release, never pulled as
# a published image. Build:
#   docker build --build-arg EKKA_VERSION=0.1.124 -t ekka-desks-enclave:0.1.124 deploy/enclave
# No Docker socket is given to this image. The service owns the shared gateway network publication.
# The default is the kit's floor (up.sh DESK_ADMIN_MIN_EKKA), never older; up.sh always passes its own.
ARG EKKA_VERSION=0.1.124
FROM gcr.io/projectsigstore/cosign:v2.6.5@sha256:ad281047f85c5e1fc6ffbc30c2b55be3b07b4032bef715a12122ce5829619aca AS cosign

FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS fetch
ARG EKKA_VERSION
RUN apk add --no-cache curl
COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign
COPY fetch-verify.sh /fetch-verify.sh
RUN sh /fetch-verify.sh "$EKKA_VERSION"

FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
ARG EKKA_VERSION
# gid 1000 is the gateway container's group (NVIDIA's image runs as 1000:1000): the interceptor socket is
# 0660 with this group, so the gateway can reach it and nobody else can.
RUN addgroup -g 1000 openshell && adduser -D -u 2000 -G openshell -h /var/lib/ekka ekka \
 && install -d -o 2000 -g 1000 -m 0750 /run/ekka \
 && install -d -o 2000 -g 1000 -m 0700 /run/ekka-backend \
 && install -d -o 2000 -g 1000 -m 0700 /var/lib/ekka/.ekka   # a new volume copies each folder's owner and mode; without
                                                             # .ekka here the ekka-home volume is root's and sign-in cannot be saved
COPY --from=fetch /out/ekka /usr/local/bin/ekka
COPY --from=fetch /out/ekka.sha256 /usr/local/share/ekka.sha256
LABEL ai.ekka.version=$EKKA_VERSION
USER 2000:1000
ENV HOME=/var/lib/ekka EKKA_HOME=/var/lib/ekka/.ekka
WORKDIR /var/lib/ekka
ENTRYPOINT ["/usr/local/bin/ekka"]
CMD ["enclave", "start"]
