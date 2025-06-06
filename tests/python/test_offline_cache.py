import atexit
import functools
import math
import shutil
import threading
from os import listdir, rmdir, stat
from os.path import join
from tempfile import mkdtemp

import pytest

import taichi as ti
from tests import test_utils

OFFLINE_CACHE_TEMP_DIR = mkdtemp()
atexit.register(lambda: rmdir(OFFLINE_CACHE_TEMP_DIR))

supported_llvm_archs = {ti.cpu, ti.cuda, ti.amdgpu}
supported_gfx_archs = {ti.opengl, ti.vulkan, ti.metal, ti.dx11}
supported_archs_offline_cache = supported_llvm_archs | supported_gfx_archs
supported_archs_offline_cache = {v for v in supported_archs_offline_cache if v in test_utils.expected_archs()}


def is_offline_cache_file(filename):
    suffixes = (".tic",)
    return filename.endswith(suffixes)


def cache_files_size(path):
    files = listdir(path)
    result = 0
    for file in files:
        if is_offline_cache_file(file):
            result += stat(join(path, file)).st_size
    return result


def expected_num_cache_files(num_kernels: int = 0) -> int:
    if num_kernels == 0:
        return 0
    # code files(*.tic) + metadata files(ticache.tcb)
    return num_kernels + 1


def tmp_offline_cache_file_path():
    return join(OFFLINE_CACHE_TEMP_DIR, str(threading.current_thread().ident))


def current_thread_ext_options():
    return {
        "offline_cache": True,
        "offline_cache_file_path": tmp_offline_cache_file_path(),
        "cuda_stack_limit": 1024,
        "device_memory_GB": 0.2,
    }


def cache_files_cnt():
    try:
        return len(listdir(tmp_offline_cache_file_path()))
    except FileNotFoundError:
        return 0


@ti.kernel
def kernel0() -> ti.i32:
    return 1


def python_kernel0():
    return 1


@ti.kernel
def kernel1(a: ti.i32, b: ti.i32, c: ti.f32) -> ti.f32:
    return a / b + c * b - c + a**2 + ti.log(c)


def python_kernel1(a, b, c):
    return a / b + c * b - c + a**2 + math.log(c)


@ti.kernel
def kernel2(n: ti.i32) -> ti.i32:
    x = 0
    for i in range(n):
        ti.atomic_add(x, 1)
    return x


def python_kernel2(n):
    return n


def kernel3(a, mat):
    mat_type = ti.types.matrix(mat.n, mat.m, ti.i32)

    @ti.kernel
    def kernel(u: ti.i32, v: mat_type) -> mat_type:
        return u * v

    return kernel(a, mat)


def python_kernel3(a, mat):
    return a * mat


@ti.func
def func_sum(lo: ti.i32, hi: ti.i32) -> ti.i32:
    res = 0
    for i in range(lo, hi):
        res += i
    return res


@ti.func
def func_mul(lo: ti.i32, hi: ti.i32) -> ti.i32:
    res = 1
    for i in range(lo, hi):
        res *= i
    return res


@ti.kernel
def kernel4(lo: ti.i32, hi: ti.i32, n: ti.i32) -> ti.i32:
    res = 0
    for i in range(n):
        res += func_sum(lo, hi)
    return res


def python_kernel4(lo: ti.i32, hi: ti.i32, n: ti.i32):
    res = 0
    for i in range(n):
        for j in range(lo, hi):
            res += j
    return res


@ti.kernel
def kernel5(lo: ti.i32, hi: ti.i32, n: ti.i32) -> ti.i32:
    res = 1
    for i in range(n):
        res *= func_mul(lo, hi)
    return res


def python_kernel5(lo: ti.i32, hi: ti.i32, n: ti.i32):
    res = 1
    for i in range(n):
        for j in range(lo, hi):
            res *= j
    return res


simple_kernels_to_test = [
    (kernel0, (), python_kernel0),
    (kernel1, (100, 200, 10.2), python_kernel1),
    (kernel2, (1024,), python_kernel2),
    # FIXME: add this kernel back once we have a better way to compare matrices
    #  with test_utils.approx()
    # (kernel3, (10, ti.Matrix([[1, 2], [256, 1024]],
    #                          ti.i32)), python_kernel3),
    # FIXME: add this kernel back once #6221 is fixed
    #   (kernel4, (1, 10, 2), python_kernel4),
    (kernel5, (1, 2, 2), python_kernel5),
]


def _test_offline_cache_dec(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        test_utils.mkdir_p(tmp_offline_cache_file_path())
        ret = None
        try:
            ret = func(*args, **kwargs)
        except Exception as e:
            raise e
        finally:
            ti.reset()
            shutil.rmtree(tmp_offline_cache_file_path())
        return ret

    return wrapped


@_test_offline_cache_dec
def _test_offline_cache_for_a_kernel(curr_arch, kernel, args, result):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    res1 = kernel(*args)
    assert added_files() == expected_num_cache_files()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(1)
    res2 = kernel(*args)
    assert res1 == test_utils.approx(result) and res1 == test_utils.approx(res2)

    ti.reset()
    assert added_files() == expected_num_cache_files(1)


@_test_offline_cache_dec
def _test_closing_offline_cache_for_a_kernel(curr_arch, kernel, args, result):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def my_init():
        ti.init(
            arch=curr_arch,
            enable_fallback=False,
            offline_cache=False,
            offline_cache_file_path=tmp_offline_cache_file_path(),
            cuda_stack_limit=1024,
            device_memory_GB=0.1,
        )

    my_init()
    res1 = kernel(*args)
    assert added_files() == expected_num_cache_files()

    my_init()
    assert added_files() == expected_num_cache_files()
    res2 = kernel(*args)

    assert res1 == test_utils.approx(result) and res1 == test_utils.approx(res2)

    ti.reset()
    assert added_files() == expected_num_cache_files()


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
def test_closing_offline_cache(curr_arch):
    for kernel, args, get_res in simple_kernels_to_test:
        _test_closing_offline_cache_for_a_kernel(curr_arch=curr_arch, kernel=kernel, args=args, result=get_res(*args))


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
def test_offline_cache_per_kernel(curr_arch):
    for kernel, args, get_res in simple_kernels_to_test:
        _test_offline_cache_for_a_kernel(curr_arch=curr_arch, kernel=kernel, args=args, result=get_res(*args))


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_multiple_ib_with_offline_cache(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def helper():
        x = ti.field(float, (), needs_grad=True)
        y = ti.field(float, (), needs_grad=True)

        @ti.kernel
        def compute_y():
            for j in range(2):
                for i in range(3):
                    y[None] += x[None]
                for i in range(3):
                    y[None] += x[None]

        x[None] = 1.0
        with ti.ad.Tape(y):
            compute_y()

        assert y[None] == 12.0
        assert x.grad[None] == 12.0

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    helper()
    assert added_files() == expected_num_cache_files()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(9)
    helper()

    ti.reset()
    assert added_files() == expected_num_cache_files(9)


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_calling_a_kernel_with_different_param_list(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    mat_type = ti.types.matrix(2, 3, ti.i32)

    @ti.kernel
    def kernel(a: mat_type, b: mat_type) -> mat_type:
        return a + 10 * b

    def np_kernel(a, b):
        return a + 10 * b

    mat1 = ti.Matrix([[1, 2, 3], [3, 2, 1]], ti.i32)
    mat2 = ti.Matrix([[1, 2, 3], [3, 2, 1]], ti.i32)
    mat3 = ti.Matrix([[1, 2, 3], [3, 2, 1]], ti.i32)
    np_mat1 = mat1.to_numpy()
    np_mat2 = mat2.to_numpy()
    np_mat3 = mat3.to_numpy()

    assert added_files() == expected_num_cache_files()
    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert (kernel(mat1, mat1).to_numpy() == np_kernel(np_mat1, np_mat1)).all()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(1)

    assert (kernel(mat1, mat1).to_numpy() == np_kernel(np_mat1, np_mat1)).all()
    assert (kernel(mat1, mat2).to_numpy() == np_kernel(np_mat1, np_mat2)).all()
    assert (kernel(mat2, mat2).to_numpy() == np_kernel(np_mat2, np_mat2)).all()
    assert (kernel(mat2, mat3).to_numpy() == np_kernel(np_mat2, np_mat3)).all()

    ti.reset()
    assert added_files() == expected_num_cache_files(1)


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_snode_reader_and_writer_with_offline_cache(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def helper():
        x = ti.field(dtype=ti.f32, shape=())
        y = ti.field(dtype=ti.f32, shape=())

        x[None] = 3.14
        y[None] = 4.14
        assert x[None] == test_utils.approx(3.14)
        assert y[None] == test_utils.approx(4.14)

        x[None] = 6.28
        y[None] = 7.28
        assert x[None] == test_utils.approx(6.28)
        assert y[None] == test_utils.approx(7.28)

    assert added_files() == expected_num_cache_files()
    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    helper()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(4)
    helper()

    ti.reset()
    assert added_files() == expected_num_cache_files(4)


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_calling_many_kernels(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def helper():
        for kernel, args, get_res in simple_kernels_to_test:
            assert kernel(*args) == test_utils.approx(get_res(*args))

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    helper()
    assert added_files() == expected_num_cache_files()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(len(simple_kernels_to_test))
    helper()
    ti.reset()
    assert added_files() == expected_num_cache_files(len(simple_kernels_to_test))


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_offline_cache_with_different_snode_trees(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def helper():
        x = ti.field(float, shape=5)

        @ti.kernel
        def trigger_compile():
            x[0] += 1

        # This case is used for testing SNodeTree storing order matters (i.e., use a ordered container such as vector instead of unordered_map or unordered_set) when generating kernel offline cache key
        # The multiple `trigger_compile` equalivant to allocate each field to a different SNodeTree
        # i.e.,
        # x = ti.field(float)
        # fb.dense(ti.i, 5).place(x)
        # fb.finalize()

        trigger_compile()
        a = ti.field(float, shape=5)
        trigger_compile()
        b = ti.field(float, shape=10)
        trigger_compile()
        c = ti.field(float, shape=5)
        trigger_compile()
        d = ti.field(float, shape=10)
        trigger_compile()
        e = ti.field(float, shape=5)
        trigger_compile()
        f = ti.field(float, shape=10)
        trigger_compile()
        g = ti.field(float, shape=5)
        trigger_compile()
        h = ti.field(float, shape=10)

        @ti.kernel
        def kernel_forward():
            for i in range(5):
                a[i] += i
                b[i] += i
                c[i] += i
                d[i] += i
                e[i] += i
                f[i] += i
                g[i] += i
                h[i] += i

        kernel_forward()

    assert added_files() == expected_num_cache_files(0)
    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    helper()

    ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(2)
    helper()

    # The number of cache file should not change
    for _ in range(5):
        ti.init(arch=curr_arch, enable_fallback=False, **current_thread_ext_options())
        assert added_files() == expected_num_cache_files(2)
        helper()


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@_test_offline_cache_dec
def test_offline_cache_with_changing_compile_config(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    @ti.kernel
    def helper():
        b = 200
        c = 0
        for i in range(b):
            c += i

    assert added_files() == expected_num_cache_files()
    ti.init(arch=curr_arch, enable_fallback=False, opt_level=0, **current_thread_ext_options())
    helper()

    ti.init(arch=curr_arch, enable_fallback=False, opt_level=1, **current_thread_ext_options())
    assert added_files() == expected_num_cache_files(1)
    helper()

    ti.reset()
    assert added_files() == expected_num_cache_files(2)
    ti.init(arch=curr_arch, enable_fallback=False, default_fp=ti.f32, **current_thread_ext_options())
    helper()

    ti.reset()
    assert added_files() == expected_num_cache_files(2)


@pytest.mark.parametrize("curr_arch", supported_archs_offline_cache)
@pytest.mark.parametrize("factor", [0.0, 0.25, 0.85, 1.0])
@pytest.mark.parametrize("policy", ["never", "version", "lru", "fifo"])
@_test_offline_cache_dec
def test_offline_cache_cleaning(curr_arch, factor, policy):
    def only_init(max_size):
        ti.init(
            arch=curr_arch,
            enable_fallback=False,
            offline_cache_cleaning_policy=policy,
            offline_cache_max_size_of_files=max_size,  # bytes
            offline_cache_cleaning_factor=factor,
            **current_thread_ext_options()
        )

    def run_simple_kernels(max_size):
        only_init(max_size)
        for kernel, args, get_res in simple_kernels_to_test:
            assert kernel(*args) == test_utils.approx(get_res(*args))

    kernel_count = len(simple_kernels_to_test)
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    assert added_files() == expected_num_cache_files()

    run_simple_kernels(1024**3)  # 1GB (>> size_of_cache_files)
    ti.reset()  # Dumping cache data
    size_of_cache_files = cache_files_size(tmp_offline_cache_file_path())
    assert added_files() == expected_num_cache_files(kernel_count)

    only_init(size_of_cache_files * 2)
    ti.reset()
    assert added_files() == expected_num_cache_files(kernel_count)

    only_init(1)  # 1B (<< size_of_cache_files)
    ti.reset()
    rem = []
    if policy in ["never", "version"]:
        rem = kernel_count
    else:
        lo = -min(kernel_count - int(factor * kernel_count), kernel_count)
        lo = kernel_count if lo == 0 else lo
        rem = len(simple_kernels_to_test[lo:])
    assert added_files() == expected_num_cache_files(rem)


# FIXME: Change to `supported_archs_offline_cache` after fixing bugs of real-function on gpu
@pytest.mark.run_in_serial
@pytest.mark.parametrize("curr_arch", {ti.cpu, ti.cuda} & supported_archs_offline_cache)
@_test_offline_cache_dec
@test_utils.test(cuda_stack_limit=8192)
def test_offline_cache_for_kernels_calling_real_func(curr_arch):
    count_of_cache_file = cache_files_cnt()

    def added_files():
        return cache_files_cnt() - count_of_cache_file

    def helper1():
        @ti.real_func
        def sum(l: ti.i32, r: ti.i32) -> ti.i32:
            if l == r:
                return l
            else:
                return sum(l, (l + r) // 2) + sum((l + r) // 2 + 1, r)

        @ti.kernel
        def get_sum() -> ti.i32:
            return sum(0, 99)

        assert get_sum() == 99 * 50

    def helper2():
        @ti.real_func
        def sum(l: ti.i32, r: ti.i32) -> ti.i32:
            if l == r:
                return l
            else:
                return sum((l + r) // 2 + 1, r) + sum(l, (l + r) // 2)

        @ti.kernel
        def get_sum() -> ti.i32:
            return sum(0, 99)

        assert get_sum() == 99 * 50

    assert added_files() == expected_num_cache_files()

    def my_init():
        ti.init(arch=curr_arch, enable_fallback=False, **{**current_thread_ext_options(), "cuda_stack_limit": 4096})

    my_init()
    helper1()

    my_init()
    assert added_files() == expected_num_cache_files(1)
    helper1()

    my_init()
    assert added_files() == expected_num_cache_files(1)
    helper2()

    my_init()
    assert added_files() == expected_num_cache_files(2)
    helper2()

    ti.reset()
    assert added_files() == expected_num_cache_files(2)
