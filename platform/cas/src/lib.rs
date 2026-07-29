//! Content-addressed store — hash types and key derivation.
//!
//! From ARCHITECTURE.md §3.2:
//! - Sha256 is not `str` — it has its own type with invariants.
//! - ArtifactRef carries hash, media_type, and size.
//! - This crate is the **pure core**: hashing, key derivation, path computation.
//!   I/O is handled by the Python/TS/Node layers.

use hex::{self, FromHex};
use serde::{Deserialize, Serialize};
use sha2::Digest;
use std::fmt;
use std::str::FromStr;

/// A SHA-256 hash value.
///
/// Parse, don't validate — construction validates the hex representation.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(into = "String", try_from = "String")]
pub struct Sha256([u8; 32]);

impl Sha256 {
    /// Compute the SHA-256 hash of bytes.
    pub fn from_bytes(data: &[u8]) -> Self {
        let mut hasher = sha2::Sha256::new();
        hasher.update(data);
        let result: [u8; 32] = hasher.finalize().into();
        Self(result)
    }

    /// Compute the SHA-256 hash of a byte stream (reader).
    pub fn from_reader(reader: &mut impl std::io::Read) -> Result<Self, std::io::Error> {
        let mut hasher = sha2::Sha256::new();
        let mut buf = [0u8; 65536];
        loop {
            let n = reader.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
        let result: [u8; 32] = hasher.finalize().into();
        Ok(Self(result))
    }

    /// Return the hex-encoded string representation.
    pub fn hex(&self) -> String {
        hex::encode(self.0)
    }

    /// Return the raw 32 bytes.
    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for Sha256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Sha256({})", &self.hex()[..16])
    }
}

impl fmt::Display for Sha256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.hex())
    }
}

impl From<Sha256> for String {
    fn from(h: Sha256) -> Self {
        h.hex()
    }
}

impl TryFrom<String> for Sha256 {
    type Error = Sha256Error;

    fn try_from(s: String) -> Result<Self, Self::Error> {
        s.parse()
    }
}

impl FromStr for Sha256 {
    type Err = Sha256Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let bytes = <[u8; 32]>::from_hex(s).map_err(|_| Sha256Error::InvalidHex(s.to_owned()))?;
        Ok(Self(bytes))
    }
}

/// Error returned when parsing a Sha256 from a string fails.
#[derive(Debug, thiserror::Error)]
pub enum Sha256Error {
    #[error("Invalid SHA-256 hex string: {0}")]
    InvalidHex(String),
}

/// An IANA media type (MIME type).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MediaType(String);

impl MediaType {
    pub fn new(value: &str) -> Result<Self, MediaTypeError> {
        if value.is_empty() || !value.contains('/') {
            return Err(MediaTypeError::Invalid(value.to_owned()));
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug, thiserror::Error)]
pub enum MediaTypeError {
    #[error("Invalid media type: {0}")]
    Invalid(String),
}

/// Reference to an immutable artifact in the content-addressed store.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArtifactRef {
    pub hash: Sha256,
    pub media_type: MediaType,
    pub size: u64,
}

/// Compute the sharded local path for a hash: `<root>/<prefix[0:2]>/<prefix[2:4]>/<hash>`.
///
/// This avoids filesystem directory bottlenecks when storing millions of blobs.
pub fn sharded_path(root: &std::path::Path, hash: &Sha256) -> std::path::PathBuf {
    let hex = hash.hex();
    root.join(&hex[..2]).join(&hex[2..4]).join(&hex)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sha256_from_bytes() {
        let h = Sha256::from_bytes(b"hello world");
        assert_eq!(
            h.hex(),
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        );
    }

    #[test]
    fn test_sha256_roundtrip() {
        let h1 = Sha256::from_bytes(b"test data");
        let s = h1.hex();
        let h2: Sha256 = s.parse().unwrap();
        assert_eq!(h1, h2);
    }

    #[test]
    fn test_sharded_path() {
        let h = Sha256::from_bytes(b"hello");
        let root = std::path::Path::new("/cache");
        let path = sharded_path(root, &h);
        assert!(path.starts_with("/cache"));
        assert_eq!(path.file_name().unwrap().to_str().unwrap().len(), 64);
    }

    #[test]
    fn test_invalid_sha256_rejected() {
        assert!("not-a-hex".parse::<Sha256>().is_err());
        assert!("abc".parse::<Sha256>().is_err());
    }

    #[test]
    fn test_serialization_roundtrip() {
        let h = Sha256::from_bytes(b"serialize me");
        let json = serde_json::to_string(&h).unwrap();
        let recovered: Sha256 = serde_json::from_str(&json).unwrap();
        assert_eq!(h, recovered);
    }
}
