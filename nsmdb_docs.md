# NSMDatabase Documentation
This documentation shows how to read and write .nsmdb files.
# What Are .nsmdb Files?
These files tell NSM what values to set where. For example, it could say that the reactor core pressure is supposed to be 160 bar.
We could just have these values hardcoded inside one of the main NSM code files, but that would quickly make those files tens or hundreds of thousands of lines and hard to read.
You'd also have to search though a giant file just to find anything. Any change would require an update to the main code.
You'd have to know how to write in Python just to add or remove things.

Essentially, these files make it easy to make NSM know what is supposed to be what.
# What's In .nsmdb Files?
A simple format that we think is easy to understand. Example with a made up "WORLD" and a made up "CHANCE" element:
```
=BEGIN=WORLD=
==OVERWRITE_LOWER_DATABASES==:==True==BOOLEAN==
==SUN_PERCENT==:==96==INT==
==TEMP==:==22.59==FLOAT==
==NAME==:=="Well, this is an example, so I didn't think of an actual name, but maybe I might in the future. Actually, I think this is funny, I'll keep it this way forever."==TEXT==
==HIDDEN==:==False==BOOLEAN==
=END=WORLD=
=BEGIN=CHANCE=
==OVERWRITE_LOWER_DATABASES==:==False==BOOLEAN==
==CHANCE==:==27.26382==FLOAT==
=END=CHANCE=
```
From [nucleares_state.py](nucleares_state.py) itself:
> ## File format
>
> Lines beginning with `#` are comments; blank lines are ignored.
>
> Block syntax::
>
> ```
> =BEGIN=COMPONENT_TAG=  
> ==OVERWRITE_LOWER_DATABASES==:==true==BOOLEAN==  
> ==SomeFloat==:==123.45==FLOAT==  
> ==SomeInt==:==10==INT==  
> ==SomeBool==:==false==BOOLEAN==  
> ==SomeText==:=="hello world"==TEXT==  
> =END=COMPONENT_TAG=  
> ```
>
> *COMPONENT_TAG* should match the in-save XML element name of a reactor
> component (e.g. `NUCLEO`, `PRESURIZADOR`, `EVAPORADOR`).
>
> Two special pseudo-tags drive generic repair / flood operations and do
> **not** need a matching XML element:
>
> `REPAIR_DEFAULTS`
> Override the target values used by `repair_all_objects`.
> Recognised keys: `Integridad`, `desgaste`, `porcentaje_roto`,
> `temperatura`.
>
> `FLUID_DEFAULTS`
> Override the fill amounts used by `flood_reserves`.
> Recognised keys: `AGUA_Cantidad`, `BORO_Cantidad`.
>
> ## Merge order
>
> Files are sorted by their numeric suffix in **ascending** order so that a
> higher-numbered file is applied *on top of* lower-numbered ones.
>
> Per-block, the `OVERWRITE_LOWER_DATABASES` flag controls the merge
> strategy:
>
> * `true`  — replace all previously loaded entries for that TAG entirely.
> * `false` (default) — merge; keys from the higher-numbered file win on
>   collision, but keys absent from it are kept from lower files.
>
> The `OVERWRITE_LOWER_DATABASES` directive itself is **never** stored as a
> data key.
>
> ## Supported value types
>
> ==============  ===================================================
> `INT`         Parsed with `int()`.
> `FLOAT`       Parsed with `float()`.
> `BOOLEAN`     `true` / `1` / `yes` → `True`, else `False`.
> `TEXT`        String; surrounding double-quotes are stripped.
> ==============  ===================================================

# How Does NSM Know They Exist?
Currently, NSM will only know the database exists if it is inside the ./data/ folder, and is named correct_safe_values-<NUM>.nsmdb.
Please only put correct safe values into these databases.
