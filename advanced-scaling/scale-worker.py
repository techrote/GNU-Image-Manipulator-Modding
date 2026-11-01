"""Local float image processing worker, GPL-3.0-or-later."""
import os, sys, json, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor'))

def configure_cpu_limit():
    """Limit pools and affinity to physical cores minus two before NumPy loads."""
    physical = None
    allowed_mask = None
    if os.name == 'nt':
        import ctypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetCurrentProcess.restype = ctypes.c_void_p
        api.GetLogicalProcessorInformationEx.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        api.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        api.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        size = ctypes.c_ulong()
        api.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        raw, offset, cores = buffer.raw, 0, []
        while offset < size.value:
            relation, record_size = struct.unpack_from('<II', raw, offset)
            if record_size < 8: raise RuntimeError('Invalid CPU topology record')
            if relation == 0:
                groups = struct.unpack_from('<H',raw,offset+30)[0]
                masks = []
                for group_index in range(groups):
                    mask, group = struct.unpack_from('<QH',raw,offset+32+16*group_index)
                    if group == 0: masks.append(mask)
                cores.append(masks)
            offset += record_size
        physical = len(cores)
        process, system = ctypes.c_size_t(), ctypes.c_size_t()
        handle = api.GetCurrentProcess()
        if not api.GetProcessAffinityMask(handle,ctypes.byref(process),ctypes.byref(system)):
            raise ctypes.WinError(ctypes.get_last_error())
        selected = []
        for masks in cores:
            mask = (masks[0] if masks else 0) & process.value
            if mask: selected.append(mask & -mask)
        limit = max(1, physical-2)
        allowed_mask = sum(selected[:limit])
        if not allowed_mask or not api.SetProcessAffinityMask(handle,allowed_mask):
            raise RuntimeError('Unable to apply the physical-core CPU limit')
        limit = min(limit,len(selected))
    else:
        pairs = set()
        for line in open('/proc/cpuinfo').read().split('\n\n'):
            entry = dict(x.split(':',1) for x in line.splitlines() if ':' in x)
            entry = {k.strip():v.strip() for k,v in entry.items()}
            if 'core id' in entry: pairs.add((entry.get('physical id','0'),entry['core id']))
        physical = len(pairs) or 1
        limit = max(1,physical-2)
        if hasattr(os,'sched_getaffinity'):
            os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:limit])
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS','OMP_THREAD_LIMIT'):
        os.environ[key] = str(limit)
    return {'physical-cores':physical,'thread-limit':limit,
            'affinity-count':allowed_mask.bit_count() if allowed_mask else limit,
            'affinity-mask':allowed_mask}

def axis_resize(data,size,axis,kernel,support,antialias=True):
    import numpy as np
    src = np.moveaxis(data,axis,0)
    count = src.shape[0]
    if count == size: return data.copy()
    factor = max(1.,count/size) if antialias else 1.
    taps = int(np.ceil(2*support*factor))+2
    output = np.empty((size,)+src.shape[1:],np.float32)
    chunk = max(1,min(size,24*1024*1024//max(1,taps*src.shape[1]*src.shape[2]*4)))
    for first in range(0,size,chunk):
        centers = (np.arange(first,min(size,first+chunk))+.5)*count/size-.5
        indices = np.floor(centers[:,None]-support*factor).astype(np.int64)+np.arange(taps)[None,:]
        distance = (centers[:,None]-indices)/factor
        weights = kernel(distance).astype(np.float32)
        weights[np.abs(distance)>=support] = 0
        sums = weights.sum(axis=1,keepdims=True)
        weights /= np.where(np.abs(sums)>1e-10,sums,1)
        sampled = src[np.clip(indices,0,count-1)]
        output[first:first+len(centers)] = np.einsum('bt,btwc->bwc',weights,sampled,optimize=False)
    return np.moveaxis(output,0,axis)

def gaussian_blur(data,sigma):
    import numpy as np
    sigma = max(.05,float(sigma))
    support = int(np.ceil(3*sigma))
    x = np.arange(-support,support+1)
    weights = np.exp(-.5*(x/sigma)**2).astype(np.float32)
    weights /= weights.sum()
    result = data
    for axis in (0,1):
        pad = [(0,0)]*3; pad[axis] = (support,support)
        padded = np.pad(result,pad,mode='edge')
        out = np.zeros_like(result)
        for offset,weight in enumerate(weights):
            slices = [slice(None)]*3
            slices[axis] = slice(offset,offset+result.shape[axis])
            out += weight*padded[tuple(slices)]
        result = out
    return result

def resize_pixels(pixels,width,height,algorithm,source_width,source_height,**options):
    import numpy as np
    from PIL import Image
    data = np.frombuffer(pixels,dtype=np.float32).reshape(source_height,source_width,4).copy()
    alpha = data[:,:,3:4].clip(0,1)
    rgb = data[:,:,:3]
    linear = algorithm == 'linear-lanczos' or options.get('linear-light',False)
    if linear: rgb = np.where(rgb<=.04045,rgb/12.92,np.maximum((rgb+.055)/1.055,0)**2.4)
    premult = np.concatenate((rgb*alpha,alpha),axis=2)
    if algorithm in ('custom-lanczos','mitchell','catmull-rom','bspline','kaiser','gaussian'):
        if algorithm in ('custom-lanczos','kaiser'):
            support = float(options.get('lobes',3))
            if algorithm == 'custom-lanczos': kernel = lambda x: np.sinc(x)*np.sinc(x/support)
            else:
                beta = options.get('kaiser-beta',6.5)
                kernel = lambda x: np.sinc(x)*np.i0(beta*np.sqrt(np.maximum(0,1-(x/support)**2)))/np.i0(beta)
        elif algorithm == 'gaussian':
            sigma = options.get('gaussian-sigma',.65)
            support = 3*sigma
            kernel = lambda x: np.exp(-.5*(x/sigma)**2)
        else:
            support = 2.
            b,c = options.get('mitchell-b',1/3),options.get('mitchell-c',1/3)
            if algorithm == 'catmull-rom': b,c = 0.,.5
            if algorithm == 'bspline': b,c = 1.,0.
            def kernel(x):
                x = np.abs(x)
                return np.where(x<1,((12-9*b-6*c)*x**3+(-18+12*b+6*c)*x**2+6-2*b)/6,
                                ((-b-6*c)*x**3+(6*b+30*c)*x**2+(-12*b-48*c)*x+8*b+24*c)/6)
        result = axis_resize(premult,width,1,kernel,support,options.get('antialias',True))
        result = axis_resize(result,height,0,kernel,support,options.get('antialias',True))
    else:
        resample = {'hamming':Image.Resampling.HAMMING,'box':Image.Resampling.BOX,
                    'bicubic':Image.Resampling.BICUBIC,'nearest':Image.Resampling.NEAREST}.get(algorithm,Image.Resampling.LANCZOS)
        channels = []
        for i in range(4):
            channel = Image.fromarray(premult[:,:,i])
            if algorithm == 'staged-lanczos':
                while channel.width>=width*2 and channel.height>=height*2:
                    channel = channel.resize((max(width,channel.width//2),max(height,channel.height//2)),Image.Resampling.BOX)
            channels.append(np.asarray(channel.resize((width,height),resample)))
        result = np.stack(channels,axis=2)
    out_alpha = result[:,:,3:4].clip(0,1)
    raw_alpha = result[:,:,3:4]
    rgb = np.divide(result[:,:,:3],raw_alpha,out=np.zeros_like(result[:,:,:3]),where=raw_alpha>1e-7)
    amount = options.get('sharpen',0.)
    if amount:
        blurred = gaussian_blur(np.concatenate((rgb*out_alpha,out_alpha),axis=2),options.get('sharpen-radius',1.))
        base = np.divide(blurred[:,:,:3],blurred[:,:,3:4],out=rgb.copy(),where=blurred[:,:,3:4]>1e-7)
        rgb += amount*(rgb-base)
    if linear: rgb = np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)
    return np.concatenate((rgb,out_alpha),axis=2).astype(np.float32).tobytes()

def filter_pixels(pixels,width,height,algorithm,alpha,radius,edge_threshold=.1):
    import numpy as np
    data = np.frombuffer(pixels,dtype=np.float32).reshape(height,width,4).copy()
    rgb,coverage = data[:,:,:3],data[:,:,3:4].clip(0,1)
    if algorithm in ('gaussian','unsharp','local-contrast'):
        sigma = radius if algorithm != 'local-contrast' else radius*3
        blurred = gaussian_blur(np.concatenate((rgb*coverage,coverage),axis=2),sigma)
        base = np.divide(blurred[:,:,:3],blurred[:,:,3:4],out=rgb.copy(),where=blurred[:,:,3:4]>1e-7)
        filtered = base if algorithm == 'gaussian' else rgb+(rgb-base)
    elif algorithm == 'bilateral':
        r = int(np.ceil(radius))
        if r>8: raise ValueError('Bilateral radius is limited to 8 pixels; use Gaussian for wider smoothing.')
        padded = np.pad(data,((r,r),(r,r),(0,0)),mode='edge')
        total = np.zeros_like(rgb); weight_sum = np.zeros_like(coverage)
        for y in range(-r,r+1):
            for x in range(-r,r+1):
                neighbor = padded[r+y:r+y+height,r+x:r+x+width]
                distance = ((neighbor[:,:,:3]-rgb)**2).mean(axis=2,keepdims=True)
                weight = np.exp(-.5*((x*x+y*y)/max(radius*radius,.01)+distance/(edge_threshold**2)))*neighbor[:,:,3:4].clip(0,1)
                total += weight*neighbor[:,:,:3]; weight_sum += weight
        filtered = np.divide(total,weight_sum,out=rgb.copy(),where=weight_sum>1e-7)
    elif algorithm in ('median','trimmed-mean'):
        import warnings
        r = int(np.ceil(radius))
        if r>8: raise ValueError('Median / trimmed-mean radius is limited to 8 pixels.')
        visible = np.where(coverage>1e-7,rgb,np.nan)
        pad = np.pad(visible,((r,r),(r,r),(0,0)),mode='edge')
        filtered = np.empty_like(rgb)
        side = 2*r+1
        rows = max(1,16*1024*1024//max(1,width*side*side*3*4))
        for y in range(0,height,rows):
            n = min(rows,height-y)
            windows = np.lib.stride_tricks.sliding_window_view(pad[y:y+n+2*r],(side,side),axis=(0,1))
            values = np.sort(windows.reshape(n,width,3,side*side),axis=-1)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',RuntimeWarning)
                if algorithm == 'median': result = np.nanmedian(values,axis=-1)
                else:
                    valid = np.isfinite(values).sum(axis=-1)
                    ranks = np.arange(side*side)
                    keep = (ranks>=np.floor(valid[...,None]*.2)) & (ranks<np.ceil(valid[...,None]*.8))
                    result = np.nanmean(np.where(keep,values,np.nan),axis=-1)
            filtered[y:y+n] = np.where(np.isfinite(result),result,rgb[y:y+n])
    else: raise ValueError('Unknown filter: '+algorithm)
    data[:,:,:3] = rgb+alpha*(filtered-rgb)
    return data.astype(np.float32).tobytes()

def dispatch(job,payload):
    import numpy as np
    op = job.get('operation','resize')
    if op == 'resize':
        return resize_pixels(payload,job['width'],job['height'],job['algorithm'],job['source-width'],job['source-height'],**job.get('options',{}))
    if op == 'filter':
        return filter_pixels(payload,job['width'],job['height'],job['algorithm'],job['alpha'],job['radius'],job.get('edge-threshold',.1))
    if op == 'blend':
        count = job['width']*job['height']*4
        a = np.frombuffer(payload[:count*4],np.float32).reshape(-1,4)
        b = np.frombuffer(payload[count*4:],np.float32).reshape(-1,4)
        out = a.copy(); out[:,:3] += job['gain']*(b[:,:3]-a[:,:3])
        return out.tobytes()
    raise ValueError('Unknown worker operation')

if __name__ == '__main__':
    info = configure_cpu_limit()
    if sys.argv[1] == '--cpu-info': print(json.dumps(info))
    elif sys.argv[1] == '--job':
        sys.stdout.buffer.write(dispatch(json.loads(sys.argv[2]),sys.stdin.buffer.read()))
    else:
        sw,sh,w,h = map(int,sys.argv[1:5])
        sys.stdout.buffer.write(resize_pixels(sys.stdin.buffer.read(),w,h,sys.argv[5],sw,sh))
