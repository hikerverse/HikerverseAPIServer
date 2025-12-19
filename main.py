from app import app

from hikerverseapiserver.routes import router



app.include_router(router)




# Add any startup logic if needed
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
