from pydantic import BaseModel, Field, FileUrl, ConfigDict
from typing import Optional

class File(BaseModel):
    path:str = Field(description="The path of the file")
    purpose:str = Field(description="The purpose of the file")

class Plan(BaseModel):
    name: str = Field(description="The name of the project")
    description: str = Field(description="The description of the project")
    features: list[str] = Field(description="The list of features the project should have. Like e.g. 'User can input two numbers', 'User can see the result', 'User can clear the result'")
    technologies: list[str] = Field(description="The technologies used in the project, e.g. 'HTML', 'CSS', 'JavaScript', 'Python'")
    files: list[File] = Field(description="The list of files the project should have. Like e.g. 'index.html', 'style.css', 'script.js'")

class ImplementationTask(BaseModel):
    filepath: str = Field(description="The path to the file to be modified")
    task_description: str = Field(description="A detailed description of the task to be performed on the file, e.g. 'add user authentication', 'implement data processing logic', etc.")

class TaskPlan(BaseModel):
    implementation_steps: list[ImplementationTask] = Field(description="A list of steps to be taken to implement the task")
    model_config = ConfigDict(extra="allow")

class CoderState(BaseModel):
    task_plan: TaskPlan = Field(description="The plan for the task to be implemented")
    current_step_idx: int = Field(0, description="The index of the current step in the implementation steps")
    current_file_content: Optional[str] = Field(None, description="The content of the file currently being edited or created")