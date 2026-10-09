"""Local, code-only speech arbitration. No native module, recording or network.

Reference matching searches recently played samples instead of assuming the
speaker and microphone callbacks share a FIFO clock. Speaker-time input needs
recent playback reference and sustained evidence of independent speech.
"""
from collections import deque
import threading
import time
import numpy as np


class Interruption:
    rate = 8000

    def __init__(self, silence_seconds=.7):
        self.silence_seconds = max(.5, min(1.2, silence_seconds))
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self._reference = np.empty(0, dtype=np.float32)
            self._last_output = -10.
            self._offset = 0.
            self._prefix = deque(maxlen=8)
            self._votes = deque(maxlen=8)
            self._last_voice = 0.
            self.active = False
            self.noise_rms = 35.
            self._learned = self._quiet = 0
            self._echo_noise = deque(maxlen=48)
            self.echo_floor = 45.
            self.raw_rms = self.clean_rms = self.match = self.novelty = 0.
            self._input_tail = np.zeros(30, dtype=np.float32)
            self._output_tail = np.zeros(30, dtype=np.float32)

    @staticmethod
    def _rms(x):
        return float(np.sqrt(np.mean(x*x))) if len(x) else 0.

    @staticmethod
    def _bandlimited(pcm, sr, previous):
        # A small streaming FIR prevents different input/output sample rates
        # from aliasing the assistant's high frequencies into apparent speech.
        index=np.arange(31)-15
        kernel=np.sinc(2*3000/sr*index)*np.hamming(31)
        kernel/=kernel.sum()
        data=np.concatenate((previous, np.asarray(pcm,dtype=np.float32).reshape(-1)))
        filtered=np.convolve(data,kernel,mode='valid')
        return filtered.astype(np.float32),data[-30:].copy()

    def playback(self, pcm, sr=24000, now=None):
        now=time.monotonic() if now is None else now
        with self._lock:
            if now-self._last_output>1.:
                self._reference=np.empty(0,dtype=np.float32)
                self._output_tail.fill(0)
                self._offset=0.
            x,self._output_tail=self._bandlimited(pcm,sr,self._output_tail)
            positions=np.arange(self._offset,len(x),sr/self.rate)
            self._offset=(positions[-1]+sr/self.rate if len(positions) else self._offset)-len(x)
            samples=np.interp(positions,np.arange(len(x)),x).astype(np.float32)
            self._reference=np.concatenate((self._reference,samples))[-int(self.rate*.8):]
            self._last_output=now

    @staticmethod
    def _match(residual, reference):
        n=len(residual)
        if len(reference)<n:
            return None,0.,0
        size=1 << (len(reference)+n-2).bit_length()
        corr=np.fft.irfft(np.fft.rfft(reference.astype(np.float64),size)*np.fft.rfft(residual[::-1].astype(np.float64),size),size)
        dots=corr[n-1:len(reference)]
        square=np.concatenate(([0.],np.cumsum(reference.astype(np.float64)**2)))
        power=square[n:]-square[:-n]
        scores=np.abs(dots)/np.sqrt(np.maximum(power*float(np.dot(residual,residual)),1.))
        # Near-silent reference windows must never win on FFT roundoff.
        scores[power<n*20**2]=0.
        scores=np.clip(scores,0.,1.)
        best=int(np.argmax(scores))
        return reference[best:best+n],float(scores[best]),best

    @staticmethod
    def _voice(x):
        if len(x)<64:
            return False
        spectrum=np.abs(np.fft.rfft((x-x.mean())*np.hanning(len(x))))**2
        freq=np.fft.rfftfreq(len(x),1/8000)
        band=spectrum[(freq>=100)&(freq<=3200)]
        if not len(band) or band.sum()<1.:
            return False
        fraction=float(band.sum()/max(spectrum.sum(),1.))
        flatness=float(np.exp(np.mean(np.log(band+1.)))/(np.mean(band)+1.))
        magnitude=np.sqrt(band)
        peaks=(magnitude[1:-1]>magnitude[:-2])&(magnitude[1:-1]>magnitude[2:])
        rich=int(np.sum(peaks&(magnitude[1:-1]>magnitude.max()*.06)))>=3
        return fraction>.55 and flatness<.45 and rich

    @staticmethod
    def _novel_energy(clean, reference):
        """Distorted echo keeps playback harmonics; a second voice adds new ones."""
        n=len(clean);window=np.hanning(n)
        far=np.abs(np.fft.rfft(reference*window))
        near=np.abs(np.fft.rfft(clean*window))**2
        mask=far>max(float(far.max())*.07,1.)
        mask=np.convolve(mask.astype(int),np.ones(3,dtype=int),mode='same')>0
        # Include weak harmonics which clipping can amplify. This protects
        # against nonlinear speakers without a dangerous loudness override.
        centered=reference-reference.mean()
        ac=np.fft.irfft(np.abs(np.fft.rfft(centered,2*n))**2,2*n)[:n]
        lo,hi=20,min(100,n-1)
        if hi>lo and ac[0]>1:
            lag=lo+int(np.argmax(ac[lo:hi]))
            if ac[lag]/ac[0]>.65:
                pitch=8000/lag
                freq=np.fft.rfftfreq(n,1/8000)
                harmonic=np.rint(freq/pitch)
                mask |= (harmonic>=1)&(np.abs(freq-harmonic*pitch)<8000/n*1.5)
        return float(near[~mask].sum()/max(near.sum(),1.))

    def _clean(self, x):
        original=self._rms(x)
        residual=x.copy();primary=None;score=0.
        # Matching pursuit removes direct echo and up to three strong reflections.
        # Weak, incidental correlations cannot be used to erase another voice.
        for _ in range(4):
            template,confidence,index=self._match(residual,self._reference)
            if template is None or confidence<.38:
                break
            if primary is None:
                primary=template;score=confidence
            # Fit the speaker/microphone coloration and fractional delay,
            # rather than subtracting a louder/quieter copy of the same waveform.
            padded=np.pad(self._reference,(3,3),mode='edge')
            basis=np.lib.stride_tricks.sliding_window_view(padded[index:index+len(x)+6],7)
            gram=np.einsum('ni,nj->ij',basis,basis,optimize=False)
            regularizer=max(float(np.trace(gram))*1e-6,1.)
            coefficients=np.linalg.solve(gram+np.eye(7)*regularizer,
                                         np.einsum('ni,n->i',basis,residual,optimize=False))
            residual-=np.einsum('ni,i->n',basis,coefficients,optimize=False)
        self.match=score
        self.novelty=self._novel_energy(residual,primary) if primary is not None else 1.
        # Projection only removes energy. Never amplify a mic sample.
        if self._rms(residual)>original:
            residual=x.copy()
        return residual

    @staticmethod
    def _pcm(x):
        expanded=np.interp(np.arange(len(x)*2)/2,np.arange(len(x)),x)
        return np.clip(expanded,-32768,32767).astype(np.int16).tobytes()

    def process(self, pcm, speaking=False, now=None):
        now=time.monotonic() if now is None else now
        with self._lock:
            samples=np.asarray(pcm,dtype=np.int16).reshape(-1)
            if not len(samples):
                return self._poll(now)
            filtered,self._input_tail=self._bandlimited(samples,16000,self._input_tail)
            x=filtered[::2].copy();x-=x.mean()
            self.raw_rms=self._rms(x)
            echo_window=speaking or now-self._last_output<1.
            clean=self._clean(x) if echo_window else x
            self.clean_rms=self._rms(clean)
            # A fitted reference can explain colored echo even when a scalar
            # correlation is modest. Require it to explain over 75% of energy.
            echo_like=echo_window and self.match>=.38 and self.clean_rms<self.raw_rms*.5
            if echo_like:
                self._echo_noise.append(self.clean_rms)
                self.echo_floor=min(400.,float(np.percentile(self._echo_noise,80)))
            threshold=max(100.,self.noise_rms*3.,self.echo_floor*1.7 if echo_window else 0.)
            voice=self._voice(clean) and self.clean_rms>threshold
            independent=False
            if echo_window:
                if self.match>.6 and self.clean_rms<self.raw_rms*.55:
                    self._learned=min(20,self._learned+1)
                if self.raw_rms<max(70.,self.noise_rms*2) and len(self._reference)>512:
                    self._quiet=min(20,self._quiet+1)
                ready=self._learned>=3 or self._quiet>=4
                # Different people can share vowel frequencies. Low correlation
                # plus predominantly unexplained speech is independent evidence;
                # frequency novelty alone would reject those real interruptions.
                independent=self.match<.6 and self.clean_rms>self.raw_rms*.65
                voice=voice and ready and not echo_like and (self.novelty>.28 or independent) and len(self._reference)>=len(x)
            elif not voice and self.raw_rms<500:
                self.noise_rms=.98*self.noise_rms+.02*max(12.,self.raw_rms)
            audio=self._pcm(clean)
            if echo_like or (echo_window and self.novelty<=.28 and not independent):
                audio=bytes(len(audio))
            if not self.active:
                self._prefix.append(audio)
                self._votes.append(bool(voice))
                # Four positive blocks, with syllable/consonant gaps (~250–500ms).
                required=4 if echo_window else 3
                if not voice or sum(self._votes)<required:
                    return []
                self.active=True;self._last_voice=now
                events=[('interrupt',None)] if speaking else []
                events.append(('start',None))
                events.extend(('audio',chunk) for chunk in self._prefix)
                self._prefix.clear();self._votes.clear()
                return events
            if voice:
                self._last_voice=now
            # Suppress recognized echo even while a user turn is open.
            return [('audio',audio)]+self._poll(now)

    def _poll(self,now):
        if self.active and now-self._last_voice>=self.silence_seconds:
            self.active=False;self._votes.clear();self._prefix.clear()
            return [('end',None)]
        return []

    def poll(self,now=None):
        with self._lock:
            return self._poll(time.monotonic() if now is None else now)

    def stop(self):
        with self._lock:
            events=[('end',None)] if self.active else []
            self.active=False;self._votes.clear();self._prefix.clear()
            return events+[('start',None),('end',None)]
