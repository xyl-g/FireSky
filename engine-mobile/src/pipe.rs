//! In-memory duplex standing in for desktop stdin/stdout (blueprint §1.1:
//! "stdin/stdout 参数化为 impl Read + impl Write，内存 duplex 承载").

use std::collections::VecDeque;
use std::io::{BufRead, Error, ErrorKind, Read, Result};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};

struct Shared {
    chunks: Mutex<VecDeque<Vec<u8>>>,
    signal: Condvar,
    closed: AtomicBool,
}

/// Input side of the duplex; clone-safe, `Drop` closes the stream.
#[derive(Clone)]
pub struct PipeWriter {
    shared: Arc<Shared>,
}

/// Output side implementing [`BufRead`] for the service framing loop.
pub struct PipeReader {
    shared: Arc<Shared>,
    staging: Vec<u8>,
    position: usize,
}

/// Create one in-memory duplex channel.
pub fn channel() -> (PipeWriter, PipeReader) {
    let shared = Arc::new(Shared {
        chunks: Mutex::new(VecDeque::new()),
        signal: Condvar::new(),
        closed: AtomicBool::new(false),
    });
    let reader = PipeReader {
        shared: shared.clone(),
        staging: Vec::new(),
        position: 0,
    };
    (PipeWriter { shared }, reader)
}

impl PipeWriter {
    /// Push one raw chunk; the service loop applies the 1 MiB line contract.
    pub fn push(&self, bytes: &[u8]) -> Result<()> {
        if bytes.is_empty() {
            return Ok(());
        }
        if self.shared.closed.load(Ordering::Acquire) {
            return Err(Error::new(ErrorKind::BrokenPipe, "service input closed"));
        }
        self.shared
            .chunks
            .lock()
            .unwrap()
            .push_back(bytes.to_vec());
        self.shared.signal.notify_one();
        Ok(())
    }

    /// Close the input side; a blocked reader observes EOF (desktop stdin EOF).
    pub fn close(&self) {
        self.shared.closed.store(true, Ordering::Release);
        self.shared.signal.notify_all();
    }
}

impl Drop for PipeWriter {
    fn drop(&mut self) {
        self.close();
    }
}

impl Read for PipeReader {
    fn read(&mut self, out: &mut [u8]) -> Result<usize> {
        let available = self.fill_buf()?;
        let take = available.len().min(out.len());
        out[..take].copy_from_slice(&available[..take]);
        self.consume(take);
        Ok(take)
    }
}

impl BufRead for PipeReader {
    fn fill_buf(&mut self) -> Result<&[u8]> {
        if self.position == self.staging.len() {
            self.staging.clear();
            self.position = 0;
            let mut chunks = self.shared.chunks.lock().unwrap();
            loop {
                match chunks.pop_front() {
                    Some(chunk) => {
                        self.staging = chunk;
                        break;
                    }
                    None if self.shared.closed.load(Ordering::Acquire) => break,
                    None => chunks = self.shared.signal.wait(chunks).unwrap(),
                }
            }
        }
        Ok(&self.staging[self.position..])
    }

    fn consume(&mut self, amount: usize) {
        self.position = (self.position + amount).min(self.staging.len());
        if self.position == self.staging.len() {
            self.staging.clear();
            self.position = 0;
        } else if self.position > 1 << 16 {
            self.staging.drain(..self.position);
            self.position = 0;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pushes_survive_across_chunk_boundaries() {
        let (writer, mut reader) = channel();
        writer.push(b"he").unwrap();
        writer.push(b"llo\nwor").unwrap();
        // Drop closes the stream: the reader observes EOF after the bytes.
        drop(writer);
        let mut text = String::new();
        reader.read_to_string(&mut text).unwrap();
        assert_eq!(text, "hello\nwor");
    }
}
