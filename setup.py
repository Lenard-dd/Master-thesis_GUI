from glob import glob

from setuptools import find_packages, setup


package_name = "hitl_gui"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/hitl_gui"]),
        (f"share/{package_name}", ["package.xml", "README.md"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/config", glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="LLM Robot Project",
    maintainer_email="your.email@example.com",
    description="Static NiceGUI prototype for robot human-in-the-loop review.",
    license="MIT",
    entry_points={"console_scripts": ["hitl_gui = hitl_gui.main:main"]},
)
