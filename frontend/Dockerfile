FROM node:22-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM dependencies AS build
COPY . .
ARG NEXT_PUBLIC_FIELDOPS_API_URL=http://localhost:8061
ENV NEXT_PUBLIC_FIELDOPS_API_URL=$NEXT_PUBLIC_FIELDOPS_API_URL
RUN npm run build

FROM dependencies AS runtime
COPY --from=build /app/dist ./dist
COPY --from=build /app/.openai ./.openai
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--ip", "0.0.0.0", "--port", "3000"]
