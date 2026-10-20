"""Bounded, lazy, immutable template variants; never caches captured pixels."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from math import ceil, floor
from threading import RLock
from time import perf_counter
import weakref

import cv2
import numpy as np


def sampling_phases(scale):
    if scale == (1.,1.):
        return ((0.,0.),)
    primary=((0.,0.),(.5,.5),(0.,.5),(.5,0.))
    return primary+tuple((x,y) for y in (0.,.25,.5,.75) for x in (0.,.25,.5,.75) if (x,y) not in primary)


def _sample_axis(source,count,scale,offset,axis):
    """Separable box integration / linear interpolation on a fixed pixel grid.

    Work in strips: temporary gather/product buffers remain below 1 MiB instead
    of growing with the full template. Canvas extent never changes the scale.
    """
    positions=np.arange(count,dtype=np.float64)
    if scale<=1:
        start=(positions-offset)/scale
        end=start+1/scale
        first=np.floor(start).astype(np.intp)
        taps=ceil(1/scale)+1
    else:
        positions=(positions-offset+.5)/scale-.5
        first=np.floor(positions).astype(np.intp)
        fraction=positions-first
        taps=2
    shape=list(source.shape);shape[axis]=count
    result=np.zeros(shape,np.float32)
    rows=max(1,16384//shape[1])
    for k in range(taps):
        indexes=first+k
        weights=(np.maximum(0,np.minimum(end,indexes+1)-np.maximum(start,indexes))*scale
                 if scale<=1 else (1-fraction if k==0 else fraction))
        weights[(indexes<0)|(indexes>=source.shape[axis])]=0
        indexes=np.clip(indexes,0,source.shape[axis]-1)
        for row in range(0,shape[0],rows):
            stop=min(shape[0],row+rows)
            if axis==1:
                values=np.take(source[row:stop],indexes,axis=1)
                result[row:stop]+=values*weights[None,:,None].astype(np.float32)
            else:
                values=np.take(source,indexes[row:stop],axis=0)
                result[row:stop]+=values*weights[row:stop,None,None].astype(np.float32)
    return result


def _project(image,mask,size,scale,offset):
    source=np.empty((*image.shape[:2],4),np.float32)
    source[:,:,:3]=image
    source[:,:,3]=1 if mask is None else (mask if mask.dtype==np.float32 else mask>0)
    source[:,:,:3]*=source[:,:,3,None]
    horizontal=_sample_axis(source,size[0],scale[0],offset[0],1)
    del source
    projected=_sample_axis(horizontal,size[1],scale[1],offset[1],0)
    del horizontal
    coverage=np.ascontiguousarray(projected[:,:,3])
    pixels=projected[:,:,:3]
    pixels/=np.maximum(coverage[:,:,None],1e-7)
    np.rint(pixels,out=pixels);np.clip(pixels,0,255,out=pixels)
    return pixels.astype(np.uint8),coverage


@dataclass(frozen=True)
class Candidate:
    image: np.ndarray
    mask: np.ndarray | None
    size: tuple[int,int]
    original_size: tuple[int,int]
    scale: tuple[float,float]
    phase: tuple[float,float]=(0.,0.)
    offset: tuple[float,float]=(0.,0.)
    color_correlation: bool=False
    cache_key: tuple=()
    parent: Candidate | None=field(default=None,repr=False,compare=False)
    structured: bool=field(init=False)

    def __post_init__(self):
        pixels=self.image.reshape(-1,3) if self.mask is None else self.image[self.mask>0]
        object.__setattr__(self,'structured',bool(pixels.size and np.any(np.ptp(pixels,axis=0))))

    @property
    def nbytes(self):
        return self.image.nbytes+(0 if self.mask is None else self.mask.nbytes)

    @property
    def content_size(self):
        return self.original_size[0]*self.scale[0],self.original_size[1]*self.scale[1]

    def part_rect(self,rect):
        from e7auto.core.types import Rect
        x=floor(self.offset[0]+rect.x*self.scale[0]);y=floor(self.offset[1]+rect.y*self.scale[1])
        right=ceil(self.offset[0]+rect.right*self.scale[0]);bottom=ceil(self.offset[1]+rect.bottom*self.scale[1])
        return Rect(x,y,right-x,bottom-y)


class ScoreData:
    """Immutable masked sufficient statistics, independent of captured pixels."""
    def __init__(self,candidate):
        t,mask=candidate.image,candidate.mask
        h,w=t.shape[:2]
        self.ys,self.xs=np.indices((h,w)).reshape(2,-1) if mask is None else np.nonzero(mask>0)
        template=t[self.ys,self.xs].astype(np.float64)
        self.weights=np.ones(len(self.ys)) if mask is None or mask.dtype!=np.float32 else mask[self.ys,self.xs].astype(np.float64)
        self.squared=self.weights*self.weights
        self.energy=float(np.einsum('n,nc,nc->',self.squared,template,template))
        self.weighted=template*self.squared[:,None]
        mean=(template*self.weights[:,None]).sum(axis=0)/self.weights.sum()
        self.centered=(template-mean)*self.squared[:,None]
        self.centered_sum=self.centered.sum(axis=0)
        self.variance=float(np.einsum('n,nc,nc->',self.squared,template-mean,template-mean))
        for value in vars(self).values():
            if isinstance(value,np.ndarray):value.setflags(write=False)

    @property
    def nbytes(self):
        return sum(value.nbytes for value in vars(self).values() if isinstance(value,np.ndarray))


@dataclass(frozen=True)
class BaselineProjection:
    image: np.ndarray
    mask: np.ndarray
    full_mask: np.ndarray


class CandidateCache:
    def __init__(self, max_bytes=64*1024*1024):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('candidate cache budget must be a positive integer')
        self.max_bytes = max_bytes
        self._lock = RLock()
        self._items = OrderedDict()
        self.live_bytes = self.peak_bytes = self.generations = self.hits = 0
        self.generation_seconds = 0.
        self._geometry = None

    def _release(self, size):
        with self._lock:
            self.live_bytes -= size

    def clear(self):
        with self._lock:
            self._items.clear()

    def geometry(self, transform):
        with self._lock:
            if self._geometry != transform:
                self._items.clear()
                self._geometry = transform

    def preprocessing(self, factory, reserved_bytes):
        """Account for live native geometry arrays owned by a temporal detector."""
        with self._lock:
            self._reserve(reserved_bytes)
            owner=factory()
            roots={}
            def visit(value):
                if isinstance(value,np.ndarray):
                    while isinstance(value.base,np.ndarray):
                        value=value.base
                    roots[id(value)]=value
                elif isinstance(value,(list,tuple)):
                    for child in value:
                        visit(child)
            for value in vars(owner).values():
                visit(value)
            size=sum(array.nbytes for array in roots.values())
            if size>reserved_bytes:
                raise ValueError('native preprocessing exceeded its reserved cache budget')
            self.live_bytes+=size
            weakref.finalize(owner,self._release,size)
            return owner

    def _reserve(self, size):
        while self._items and self.live_bytes+size > self.max_bytes:
            self._items.popitem(last=False)
        if self.live_bytes+size > self.max_bytes:
            raise ValueError('template candidate and generation buffers exceed configured cache budget')
        self.peak_bytes = max(self.peak_bytes,self.live_bytes+size)

    def baseline_projection(self, data, scale):
        """Cache the validated 1:1 backend's AREA/NEAREST coarse pair separately."""
        image, mask = data.image, data.mask
        identity = getattr(data, 'identity', None)
        if identity is None:
            identity = sha256(image.tobytes()+(b'' if mask is None else mask.tobytes())).digest()
        key = ('baseline-projection', identity, scale)
        with self._lock:
            if key in self._items:
                self.hits += 1
                result = self._items.pop(key); self._items[key] = result
                return result
            h, w = image.shape[:2]
            self._reserve(h*w*5+65536)
            owned = []
            if mask is None:
                mask = np.full((h,w),255,np.uint8); owned.append(mask)
            small_image, small_mask = image, mask
            if scale < 1:
                size = (max(1,round(w*scale)),max(1,round(h*scale)))
                small_image = cv2.resize(image,size,interpolation=cv2.INTER_AREA)
                small_mask = cv2.resize(mask,size,interpolation=cv2.INTER_NEAREST)
                owned.extend((small_image,small_mask))
            for array in owned:array.setflags(write=False)
            result = BaselineProjection(small_image,small_mask,mask)
            size = sum(array.nbytes for array in owned)
            self.live_bytes += size
            weakref.finalize(result,self._release,size)
            self._items[key] = result
            return result

    def get(self,data,scale,*,phase=(0.,0.),blur=False):
        """Project from original pixels once; retain this owner while using views."""
        image,mask=data.image,data.mask
        height,width=image.shape[:2]
        scale=tuple(float(v) for v in scale);phase=tuple(float(v) for v in phase)
        if len(scale)!=2 or not all(np.isfinite(v) and v>0 for v in scale):
            raise ValueError('invalid template scale')
        if phase not in sampling_phases(scale):
            raise ValueError('unsupported template sampling phase')
        if not image.size or (mask is not None and not np.any(mask)):
            raise ValueError('empty template candidate support')
        identity=getattr(data,'identity',None)
        if identity is None:
            identity=sha256(image.tobytes()+(b'' if mask is None else mask.tobytes())).digest()
        key=(identity,scale,phase,blur,'fixed-grid-v2')
        original_size=(width,height)
        if scale==(1.,1.) and not blur:
            return Candidate(image,mask,original_size,original_size,scale,color_correlation=mask is None,cache_key=key)
        offset=(0.,0.) if scale==(1.,1.) else (1+phase[0],1+phase[1])
        size=original_size if scale==(1.,1.) else (ceil(width*scale[0])+3,ceil(height*scale[1])+3)
        with self._lock:
            if key in self._items:
                self.hits+=1
                result=self._items.pop(key);self._items[key]=result
                return result
            started=perf_counter()
            # Original float input + one separable output, or final RGB/coverage;
            # strip gathers/products and axis-weight arrays get a bounded reserve.
            intermediate=height*size[0]
            reserve=max(16*(width*height+intermediate),16*(intermediate+size[0]*size[1]),
                        23*size[0]*size[1])+32*min(16384,max(intermediate,size[0]*size[1]))+128*max(size)+65536
            self._reserve(reserve)
            if scale==(1.,1.):
                pixels=image.copy();coverage=None if mask is None else mask.copy()
            else:
                pixels,coverage=_project(image,mask,size,scale,offset)
            if blur:
                pixels=cv2.GaussianBlur(pixels,(3,3),0)
            pixels.setflags(write=False)
            if coverage is not None:coverage.setflags(write=False)
            result=Candidate(pixels,coverage,size,original_size,scale,phase,offset,mask is None,key)
            self.live_bytes+=result.nbytes
            weakref.finalize(result,self._release,result.nbytes)
            self._items[key]=result;self.generations+=1
            self.generation_seconds+=perf_counter()-started
            return result

    def score_data(self,candidate):
        key=('scores',candidate.cache_key)
        with self._lock:
            if key in self._items:
                self.hits+=1
                result=self._items.pop(key);self._items[key]=result
                return result
            count=candidate.image.shape[0]*candidate.image.shape[1] if candidate.mask is None else int(np.count_nonzero(candidate.mask))
            self._reserve(count*200+65536)
            result=ScoreData(candidate)
            self.live_bytes+=result.nbytes
            weakref.finalize(result,self._release,result.nbytes)
            self._items[key]=result
            return result

    def part(self, parent, rect):
        """View one projected parent grid; retain its owner and byte accounting."""
        key = ('component-part-v1', parent.cache_key, rect)
        with self._lock:
            if key in self._items:
                self.hits += 1
                result = self._items.pop(key); self._items[key] = result
                return result
            sample = parent.part_rect(rect)
            sx, sy = parent.scale
            # Include interpolation support at the original part's outer edges.
            px, py = max(1, ceil(sx/2)), max(1, ceil(sy/2))
            x, y = max(0, sample.x-px), max(0, sample.y-py)
            right, bottom = min(parent.size[0], sample.right+px), min(parent.size[1], sample.bottom+py)
            image = parent.image[y:bottom, x:right]
            mask = None if parent.mask is None else parent.mask[y:bottom, x:right]
            offset = (parent.offset[0]+rect.x*sx-x, parent.offset[1]+rect.y*sy-y)
            self._reserve((right-x)*(bottom-y)*4)
            result = Candidate(image, mask, (right-x, bottom-y), (rect.width, rect.height),
                               parent.scale, parent.phase, offset, parent.color_correlation,
                               key, parent=parent)
            # These arrays are views. The parent stays charged until every view dies.
            self._items[key] = result
            return result
