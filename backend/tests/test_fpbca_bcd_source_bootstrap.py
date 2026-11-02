from pathlib import Path
import json,subprocess,sys
import pytest
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('entry',['run_demo.py','workflow_control.py','mainline_runtime.py','run_pipeline.py'])
def test_cached_foreign_package_rejected_before_any_project_loader(tmp_path,entry):
    foreign=tmp_path/'foreign';core=foreign/'core';core.mkdir(parents=True)
    marker=tmp_path/'FOREIGN_EXECUTED'
    (core/'config.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\nraise RuntimeError("FOREIGN_LOADER")',encoding='utf-8')
    (core/'hashing.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\nraise RuntimeError("FOREIGN_LOADER")',encoding='utf-8')
    code='''import runpy,sys,types\nfrom pathlib import Path\nforeign=Path(F);m=types.ModuleType('core');m.__file__=str(foreign/'core/__init__.py');m.__path__=[str(foreign/'core')];sys.modules['core']=m\nsys.path[:0]=[str(R/'scripts'),str(R/'backend'),str(foreign)]\ntry:runpy.run_path(str(R/'scripts'/ENTRY),run_name='fpbca_test_import')\nexcept BaseException as exc:print(type(exc).__name__,str(exc))\n'''
    code='from pathlib import Path\nR=Path('+repr(str(ROOT))+');F='+repr(str(foreign))+';ENTRY='+repr(entry)+'\n'+code
    process=subprocess.run([sys.executable,'-I','-B','-c',code],capture_output=True,text=True,timeout=45)
    assert process.returncode==0,process.stderr
    assert not marker.exists(),process.stdout
    assert 'source' in process.stdout.lower() or 'foreign' in process.stdout.lower(),process.stdout
