FROM kalilinux/kali-rolling

ARG INSTALL_CTF_TOOLS=1
ARG CTF_INSTALL_MODE=all
ARG INSTALL_SKILL_TOOLS=1
ARG SKILL_TOOL_PROFILE=core
ARG CTF_INSTALLER_SHA=unknown

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/root/.local/bin:/root/go/bin:/root/.ctf-tools/venv/bin:/root/.aegissec-skill-tools/venv/bin:${PATH}"

LABEL aegissec.install_ctf_tools="${INSTALL_CTF_TOOLS}"
LABEL aegissec.ctf_install_mode="${CTF_INSTALL_MODE}"
LABEL aegissec.install_skill_tools="${INSTALL_SKILL_TOOLS}"
LABEL aegissec.skill_tool_profile="${SKILL_TOOL_PROFILE}"
LABEL aegissec.ctf_installer_sha="${CTF_INSTALLER_SHA}"

RUN apt-get update \
    && apt-get install -y \
        bash \
        build-essential \
        ca-certificates \
        cargo \
        curl \
        git \
        golang-go \
        jq \
        kali-linux-default \
        libcap2-bin \
        libffi-dev \
        libssl-dev \
        pkg-config \
        procps \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        ruby \
        ruby-dev \
        sudo \
        tini \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/aegissec

COPY scripts/install_ctf_tools.sh ./scripts/install_ctf_tools.sh
COPY docker/kali/install_skill_tools.sh ./docker/kali/install_skill_tools.sh

RUN chmod +x ./scripts/install_ctf_tools.sh ./docker/kali/install_skill_tools.sh \
    && if [ "${INSTALL_CTF_TOOLS}" = "1" ]; then \
        ./scripts/install_ctf_tools.sh "${CTF_INSTALL_MODE}"; \
       else \
        echo "[aegissec-kali] Skip CTF baseline installation"; \
       fi \
    && if [ "${INSTALL_SKILL_TOOLS}" = "1" ]; then \
        ./docker/kali/install_skill_tools.sh "${SKILL_TOOL_PROFILE}"; \
       else \
        echo "[aegissec-kali] Skip skill extras installation"; \
       fi

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/zsh"]
